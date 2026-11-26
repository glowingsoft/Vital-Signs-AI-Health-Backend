# python_auth

FastAPI dependencies that verify the JWT access tokens issued by the Vital Signs NestJS backend. The Python AI service checks tokens on its own, with no call to NestJS or MongoDB.

> **Before you deploy:** configure the same strong, private `JWT_SECRET` in NestJS and the Python API. NestJS loads `.env` before importing the application; both services reject a missing secret, and Python also rejects the legacy public fallback.

## What NestJS does (findings)

| Item | Value | Where |
|---|---|---|
| Where tokens are created | `generateToken()` in **`src/guards/auth.guard.ts`** (not in UserService). Called from `UserService` (signIn, socialAuth, verifyOtp, refreshToken) and `AdminService.signIn` | `src/guards/auth.guard.ts` |
| Library | `jsonwebtoken.sign()` directly. The shared `JwtModule` and token helper use the same environment secret | `src/user/user.module.ts`, `src/guards/auth.guard.ts` |
| Algorithm | **HS256** (jsonwebtoken default, confirmed from a real token header) | — |
| Secret | env **`JWT_SECRET`**; no default (legacy public fallback rejected) | `src/constants/constants.ts` |
| Access token lifetime | **1 hour** (`expiresIn: '1h'`) | `auth.guard.ts:87` |
| Refresh token lifetime | **1 day** (`expiresIn: '1d'`) | `auth.guard.ts:99` |

**Access token payload:** `_id`, `email`, `name`, `user_type`, `timezone`, `is_refresh: false`, `iat`, `exp`.
- `_id` is the MongoDB ObjectId as a 24-char hex string; `user_type` is a number.
- `email` and `name` are **decrypted plain text** in the access token. In the refresh token they are AES-encrypted.
- Python only uses **`_id` → `user_id`** and **`user_type`**. It also checks `exp`, `iat` and `is_refresh`. Email, name and timezone are never passed to the AI code.

**Refresh vs access:** both use the **same secret and algorithm**. The only difference is the `is_refresh` claim (`false` or `true`) and the lifetime. Python rejects any token whose `is_refresh` is not exactly `false`, which includes refresh tokens and tokens missing the claim.

**Roles** (`src/user/dto/user.dto.ts:13-18`), copied exactly in `roles.py`:

| Name | Value |
|---|---|
| Admin | 0 |
| User (patient) | 1 |
| Doctor | 2 |
| Guest | 3 |
| Supervisor | 4 |
| Labeler | 5 |
| Developer | 6 |

**Guards in NestJS:**
- `AuthGuard` (global `APP_GUARD`) skips routes marked `@Public()`. Otherwise it reads `Authorization: Bearer <token>`, verifies it, and sets `req.user` to the payload. Missing token → 401. Invalid or expired token → **403** (`auth.guard.ts:41`).
- `AccessGuard` (global) reads `@Access(...types)`. Without `@Access`, any authenticated user is allowed. Otherwise `req.user.user_type` must be in the list, or it throws **401** (`access.guard.ts:39`).
- In Python, `current_user` matches AuthGuard and `require_roles(...)` matches `@Access(...)`. Python uses the standard codes, **401 for a bad token and 403 for the wrong role**, which is the reverse of what NestJS returns today. See issue #5.

## Files

```
python_auth/
├── __init__.py        makes the folder a Python package (import python_auth.auth)
├── auth.py            current_user(), require_roles(), verify_access_token(), AuthUser
├── roles.py           UserType IntEnum (same names and numbers as NestJS)
├── example_route.py   two example routes
├── test_auth.py       pytest suite (32 tests)
└── README.md

Dependencies (PyJWT) are in the project's requirements.txt; JWT_SECRET is listed in the project's .env.example.
```

## Install and test

```bash
# from the project root, with the project's .venv active
pip install -r requirements-dev.txt
pytest python_auth -q          # tests set their own throwaway secret
```

Tested on Python 3.9 with the pinned versions. The tests were also checked against tokens produced by the real NestJS `generateToken()`.

## Use

```python
from fastapi import Depends
from python_auth.auth import AuthUser, current_user, require_roles
from python_auth.roles import UserType

@app.get("/ai/me")
def me(user: AuthUser = Depends(current_user)):             # any logged-in user
    ...

@app.post("/ai/analyze")
def analyze(user: AuthUser = Depends(require_roles(UserType.User))):   # patients only
    ...
```

`AuthUser` has two fields: `user_id: str` and `user_type: UserType`. For non-HTTP code (such as a WebSocket handshake), call `verify_access_token(token)`, which raises `InvalidToken`.

The modules use flat imports (`from roles import ...`). Keep the files together in one directory on your import path. If you move them into a package, change the imports to relative ones (`from .roles import ...`).

**`JWT_SECRET` must be exactly the value NestJS signs with.** It is read once at import. The app fails to start if it is missing, empty, or equal to the public NestJS fallback. There is no default.

To check that a secret matches what production actually signs with, take a real access token from the app (for example, from a login response) and run:

```bash
JWT_SECRET='<candidate>' python -c "import sys, auth; print(auth.verify_access_token(sys.argv[1]))" '<token>'
```

## Limits (by design)

Python cannot see the database, so a token stays valid until it expires (up to 1 hour) even if the user logs out, is blocked or is deleted. NestJS behaves the same way: its AuthGuard does not check the database either.

## Issues found

The Node token paths now use the required environment secret, and startup rejects the old public fallback. Deploy both services with the same private value and restart them; tokens issued with the previous fallback will not verify.

### 1. Critical: socket connections accept unverified user IDs
`resolveSubjectId()` can fall back to `auth.subjectId` or `query.subjectId` (`chat/chat.gateway.ts`) without verifying that identity. Any client may be able to connect as another user and access their chat or call events.
**Fix:** reject the connection when the token is missing or invalid, and remove the `subjectId` fallback.

### 2. High: NestJS accepts refresh tokens as access tokens
`AuthGuard` (`auth.guard.ts:35-39`) never checks `is_refresh`, and the gateway doesn't either (`chat.gateway.ts:134`). A refresh token is valid for 1 day, so it works as a 1-day access token on every route. Python rejects these tokens.
**Fix:** after verifying, add `if (payload.is_refresh !== false) throw new UnauthorizedException()`. A stronger fix is a separate refresh secret or a distinct `typ` or `aud` claim.

### 3. High: PII encryption keys have built-in defaults
`src/utils/encrptdecrpt.ts` uses defaults if `ENCRYPTION_KEY` and `HASH_SECRET` are not configured. If existing data was encrypted with those defaults, changing the keys without a migration would make it unreadable and break email lookups. Verify the deployed values before changing them.

### 4. Medium: status codes are swapped compared with the standard
NestJS returns 403 for an invalid or expired token (`auth.guard.ts:41`) and 401 for the wrong role (`access.guard.ts:36, :39`). The usual convention is the opposite, which is what Python does. The mobile app must refresh its token on **403** from NestJS but on **401** from Python.
**Fix:** use `UnauthorizedException` in AuthGuard and `ForbiddenException` in AccessGuard. Update the app's refresh logic at the same time.

### 5. Low: access tokens contain plain-text email and name
`auth.guard.ts:79-80` decrypts the email and name into the access token. A JWT is only signed, not encrypted, so anyone holding the token can read them, including logs and proxies.
**Fix:** keep only `_id`, `user_type` and `timezone` in the token.
