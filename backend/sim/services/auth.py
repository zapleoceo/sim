"""Admin authentication: argon2 passwords, server-side sessions, CSRF token, login rate limit."""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import AdminUser, AuthSession
from sim.adapters.db.repositories.accounts import AdminUserRepo, AuthSessionRepo
from sim.clock import Clock, utcnow
from sim.domain.enums import Role

INVALID_CREDENTIALS = "Invalid username or password"
hasher = PasswordHasher()
DUMMY_HASH = hasher.hash("sim-dummy-password")  # burns the same time for unknown users


class AuthError(Exception):
    """Wrong credentials (the message is identical for unknown user and wrong password)."""


class TooManyAttempts(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__("Too many login attempts, try again later")
        self.retry_after_s = retry_after_s


class LoginRateLimiter:
    """Sliding window of FAILED attempts per key (username and client address)."""

    def __init__(self, max_failures: int = 5, window_s: int = 900) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.failures: dict[str, deque[float]] = defaultdict(deque)

    def check(self, *keys: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for key in keys:
            bucket = self.failures[key]
            while bucket and now - bucket[0] > self.window_s:
                bucket.popleft()
            if len(bucket) >= self.max_failures:
                raise TooManyAttempts(int(self.window_s - (now - bucket[0])) + 1)

    def record_failure(self, *keys: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for key in keys:
            self.failures[key].append(now)

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
        clock: Clock = utcnow,
    ) -> None:
        self.users = AdminUserRepo(session)
        self.sessions = AuthSessionRepo(session)
        self.ttl = timedelta(hours=ttl_h)
        self.limiter = limiter
        self.clock = clock

    async def create_user(self, username: str, password: str, role: Role) -> AdminUser:
        return await self.users.add(
            AdminUser(username=username, password_hash=hasher.hash(password), role=role)
        )

    async def bootstrap_owner(self, username: str, password: str) -> AdminUser | None:
        """Create the first owner from settings when there are no admins yet (idempotent)."""
        if not username or not password or await self.users.count():
            return None
        return await self.create_user(username, password, Role.OWNER)

    async def login(self, username: str, password: str, client_addr: str = "") -> LoginResult:
        keys = (f"user:{username.lower()}", f"addr:{client_addr}")
        self.limiter.check(*keys)
        user = await self.users.by_username(username)
        if not self.password_ok(user.password_hash if user else DUMMY_HASH, password) or (
            user is None or not user.is_active
        ):
            self.limiter.record_failure(*keys)
            raise AuthError(INVALID_CREDENTIALS)
        self.limiter.reset(keys[0])
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

    @staticmethod
    def password_ok(password_hash: str, password: str) -> bool:
        try:
            return hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

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
