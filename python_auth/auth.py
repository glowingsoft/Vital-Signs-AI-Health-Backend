"""Verify access tokens issued by the Vital Signs NestJS backend.

Tokens are created by generateToken() in src/guards/auth.guard.ts:
HS256, signed with JWT_SECRET, access tokens expire after 1 hour, and
access/refresh tokens share one secret and differ only by `is_refresh`.

This module only verifies tokens. It never talks to NestJS or MongoDB.
"""

import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from python_auth.roles import UserType

ALGORITHM = "HS256"

# Hard-coded fallback in the NestJS repo (src/guards/auth.guard.ts:14).
# It is public, so anyone could forge tokens with it. Refuse to run with it.
_PUBLIC_FALLBACK_SECRET = "Some Complex Secrete Value"

_OBJECT_ID = re.compile(r"^[0-9a-f]{24}$")


def _load_secret() -> str:
    secret = os.environ.get("JWT_SECRET", "")
    if not secret.strip():
        raise RuntimeError(
            "JWT_SECRET is not set. It must be the same value the NestJS backend signs tokens with."
        )
    if secret == _PUBLIC_FALLBACK_SECRET:
        raise RuntimeError(
            "JWT_SECRET is the hard-coded NestJS fallback, which is public. "
            "Fix the NestJS secret loading first (see README, Issues found #1)."
        )
    return secret


# Read once at import, so a missing secret stops the app at startup.
_SECRET = _load_secret()


@dataclass(frozen=True)
class AuthUser:
    """The only identity the AI code gets. Email and name are left out on purpose."""

    user_id: str
    user_type: UserType


class InvalidToken(Exception):
    """Raised by verify_access_token() for any token that must be rejected."""


def verify_access_token(token: str) -> AuthUser:
    """Verify a NestJS access token and return the caller's identity.

    Usable outside FastAPI too (e.g. a WebSocket handshake).
    Raises InvalidToken for expired, tampered, refresh or malformed tokens.
    """
    try:
        payload = jwt.decode(
            token,
            _SECRET,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "iat"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise InvalidToken("Token expired") from e
    except jwt.PyJWTError as e:
        raise InvalidToken("Invalid token") from e

    # Access and refresh tokens share a secret; `is_refresh` is the only difference.
    # Require an explicit `false` so refresh tokens (and tokens without the flag) fail.
    if payload.get("is_refresh") is not False:
        raise InvalidToken("Refresh tokens cannot be used as access tokens")

    user_id = payload.get("_id")
    if not isinstance(user_id, str) or not _OBJECT_ID.match(user_id):
        raise InvalidToken("Invalid token subject")

    raw_type = payload.get("user_type")
    # bool is a subclass of int in Python; exclude it explicitly.
    if isinstance(raw_type, bool) or not isinstance(raw_type, int):
        raise InvalidToken("Invalid user_type")
    try:
        user_type = UserType(raw_type)
    except ValueError as e:
        raise InvalidToken("Invalid user_type") from e

    return AuthUser(user_id=user_id, user_type=user_type)


_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> AuthUser:
    """FastAPI dependency: 401 unless the request carries a valid access token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Missing bearer token")
    try:
        return verify_access_token(credentials.credentials)
    except InvalidToken as e:
        raise _unauthorized(str(e)) from e


def require_roles(*roles: UserType) -> Callable[..., AuthUser]:
    """FastAPI dependency factory: 403 unless the caller has one of `roles`.

    Same rule as the NestJS AccessGuard / @Access(...) decorator.
    """
    if not roles:
        raise ValueError("require_roles() needs at least one role")
    allowed = frozenset(UserType(r) for r in roles)

    def dependency(user: AuthUser = Depends(current_user)) -> AuthUser:
        if user.user_type not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized access",
            )
        return user

    return dependency
