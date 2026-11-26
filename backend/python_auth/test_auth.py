"""Run from the project root: pytest python_auth"""

import os
import subprocess
import sys
import time
from pathlib import Path

import jwt
import pytest

TEST_SECRET = "test-secret-for-pytest-only-0123456789"
os.environ["JWT_SECRET"] = TEST_SECRET


from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from python_auth.auth import verify_access_token, AuthUser, _load_secret, current_user, require_roles  # noqa: E402
from python_auth.roles import UserType  # noqa: E402

PROJECT_ROOT = Path(__file__).parent.parent
USER_ID = "650f1c2e9b1d4a0012345678"


def make_token(
    secret=TEST_SECRET,
    user_type=UserType.User,
    is_refresh=False,
    expires_in=3600,
    algorithm="HS256",
    **overrides,
):
    """Same payload shape as generateToken() in src/guards/auth.guard.ts."""
    now = int(time.time())
    payload = {
        "_id": USER_ID,
        "email": "patient@example.com",
        "name": "Test Patient",
        "user_type": user_type,
        "timezone": "UTC",
        "is_refresh": is_refresh,
        "iat": now,
        "exp": now + expires_in,
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not ...}
    return jwt.encode(payload, secret, algorithm=algorithm)


app = FastAPI()


@app.get("/me")
def me(user: AuthUser = Depends(current_user)):
    return {"user_id": user.user_id, "user_type": int(user.user_type)}


@app.get("/patients-only")
def patients_only(user: AuthUser = Depends(require_roles(UserType.User))):
    return {"ok": True}


@app.get("/supervisors")
def supervisors(user: AuthUser = Depends(require_roles(UserType.Supervisor, UserType.Labeler))):
    return {"ok": True}


@app.get("/staff")
def staff(user: AuthUser = Depends(require_roles(UserType.Doctor, UserType.Admin))):
    return {"ok": True}


client = TestClient(app)


def get(path, token=None, scheme="Bearer"):
    headers = {"Authorization": f"{scheme} {token}"} if token is not None else {}
    return client.get(path, headers=headers)


# --- valid token -----------------------------------------------------------

def test_valid_token_returns_id_and_role_only():
    r = get("/me", make_token())
    assert r.status_code == 200
    assert r.json() == {"user_id": USER_ID, "user_type": 1}


@pytest.mark.parametrize("quote", ['"', "'"])
def test_secret_with_matching_outer_quotes_is_unwrapped(monkeypatch, quote):
    secret = "quoted-test-secret-0123456789"
    monkeypatch.setenv("JWT_SECRET", f"{quote}{secret}{quote}")

    assert _load_secret() == secret


@pytest.mark.parametrize("role", [UserType.Supervisor, UserType.Labeler, UserType.Developer])
def test_new_roles_are_accepted(role):
    r = get("/me", make_token(user_type=int(role)))
    assert r.status_code == 200
    assert r.json() == {"user_id": USER_ID, "user_type": int(role)}


def test_auth_user_does_not_carry_email():
    # The name is kept only for staff actions (who changed a medical source); the email is never kept.
    assert set(AuthUser.__dataclass_fields__) == {"user_id", "user_type", "name"}


def test_auth_user_name_comes_from_the_token():
    assert verify_access_token(make_token(name="  Jane   Admin ")).name == "Jane Admin"
    assert verify_access_token(make_token(name=None)).name is None
    assert verify_access_token(make_token(name=123)).name is None


# --- 401 cases --------------------------------------------------------------

def test_missing_header_is_401():
    r = get("/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_wrong_scheme_is_401():
    assert get("/me", make_token(), scheme="Basic").status_code == 401


def test_expired_token_is_401():
    r = get("/me", make_token(expires_in=-10))
    assert r.status_code == 401
    assert r.json()["detail"] == "Token expired"


def test_wrong_secret_is_401():
    assert get("/me", make_token(secret="some-other-secret-value-xyz")).status_code == 401


def test_invalid_token_is_logged_without_logging_token(caplog):
    token = make_token(secret="some-other-secret-value-xyz")

    r = get("/me", token)

    assert r.status_code == 401
    assert "JWT verification failed: InvalidSignatureError" in caplog.text
    assert "Authentication rejected: Invalid token" in caplog.text
    assert token not in caplog.text


def test_tampered_payload_is_401():
    header, payload, sig = make_token(user_type=UserType.User).split(".")
    forged_payload = jwt.utils.base64url_encode(
        jwt.utils.base64url_decode(payload).replace(b'"user_type":1', b'"user_type":0')
    ).decode()
    assert get("/me", f"{header}.{forged_payload}.{sig}").status_code == 401


def test_refresh_token_is_rejected():
    r = get("/me", make_token(is_refresh=True, expires_in=86400))
    assert r.status_code == 401
    assert "Refresh" in r.json()["detail"]


def test_token_without_is_refresh_flag_is_rejected():
    assert get("/me", make_token(is_refresh=...)).status_code == 401


def test_other_algorithm_is_rejected():
    assert get("/me", make_token(algorithm="HS512")).status_code == 401


def test_unsigned_alg_none_is_rejected():
    token = jwt.encode({"_id": USER_ID, "user_type": 0, "is_refresh": False,
                        "iat": int(time.time()), "exp": int(time.time()) + 60},
                       key=None, algorithm="none")
    assert get("/me", token).status_code == 401


def test_missing_exp_is_rejected():
    assert get("/me", make_token(exp=...)).status_code == 401


@pytest.mark.parametrize("bad", [7, -1, "1", True, None])
def test_invalid_user_type_is_401(bad):
    assert get("/me", make_token(user_type=bad)).status_code == 401


@pytest.mark.parametrize("bad", ["", "not-an-object-id", 12345, None])
def test_invalid_user_id_is_401(bad):
    assert get("/me", make_token(_id=bad)).status_code == 401


# --- roles (403) -------------------------------------------------------------

def test_wrong_role_is_403():
    r = get("/patients-only", make_token(user_type=UserType.Doctor))
    assert r.status_code == 403


def test_right_role_is_200():
    assert get("/patients-only", make_token(user_type=UserType.User)).status_code == 200


@pytest.mark.parametrize("role,expected", [
    (UserType.Admin, 200), (UserType.Doctor, 200), (UserType.User, 403), (UserType.Guest, 403),
])
def test_multiple_allowed_roles(role, expected):
    assert get("/staff", make_token(user_type=role)).status_code == expected


@pytest.mark.parametrize("role,expected", [
    (UserType.Supervisor, 200), (UserType.Labeler, 200), (UserType.Developer, 403),
    (UserType.Admin, 403), (UserType.Doctor, 403), (UserType.User, 403), (UserType.Guest, 403),
])
def test_new_roles_in_require_roles(role, expected):
    assert get("/supervisors", make_token(user_type=role)).status_code == expected


@pytest.mark.parametrize("role", [UserType.Supervisor, UserType.Labeler, UserType.Developer])
def test_new_roles_are_denied_where_not_listed(role):
    assert get("/patients-only", make_token(user_type=role)).status_code == 403
    assert get("/staff", make_token(user_type=role)).status_code == 403


def test_role_check_still_requires_valid_token():
    assert get("/staff", make_token(user_type=UserType.Admin, expires_in=-10)).status_code == 401


def test_require_roles_needs_at_least_one_role():
    with pytest.raises(ValueError):
        require_roles()


# --- startup -----------------------------------------------------------------

def _import_auth_with(secret):
    env = {k: v for k, v in os.environ.items() if k != "JWT_SECRET"}
    if secret is not None:
        env["JWT_SECRET"] = secret
    return subprocess.run([sys.executable, "-c", "import python_auth.auth"],
                          cwd=PROJECT_ROOT, env=env, capture_output=True, text=True)


def test_startup_fails_without_secret():
    r = _import_auth_with(None)
    assert r.returncode != 0
    assert "JWT_SECRET is not set" in r.stderr


def test_startup_fails_with_public_nestjs_fallback_secret():
    r = _import_auth_with("Some Complex Secrete Value")
    assert r.returncode != 0
    assert "fallback" in r.stderr


def test_roles_match_nestjs_enum():
    assert {r.name: r.value for r in UserType} == {
        "Admin": 0, "User": 1, "Doctor": 2, "Guest": 3, "Supervisor": 4, "Labeler": 5, "Developer": 6,
    }
