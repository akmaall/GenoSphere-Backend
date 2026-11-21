from datetime import timedelta

import pytest

from app.deps import ensure_owner
from app.errors import APIError
from app.models import User
from app.security import create_access_token

REGISTER = {
    "full_name": "Dr. John Doe",
    "email": "Peneliti@Institusi.ac.id",
    "username": "johndoe",
    "password": "rahasia123",
    "confirm_password": "rahasia123",
}


def register(client, **overrides):
    return client.post("/api/register", json={**REGISTER, **overrides})


def login(client, username="peneliti@institusi.ac.id", password="rahasia123"):
    return client.post("/api/login", data={"username": username, "password": password})


def code(resp):
    return resp.json()["detail"]["code"]


# ---------- register ----------
def test_register_success_and_no_password_leak(client):
    r = register(client)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "peneliti@institusi.ac.id"  # dinormalkan ke huruf kecil
    assert "password" not in body and "password_hash" not in body


def test_register_duplicate_email_case_insensitive(client):
    register(client)
    r = register(client, email="PENELITI@institusi.ac.id", username="lain")
    assert r.status_code == 409 and code(r) == "EMAIL_ALREADY_REGISTERED"


def test_register_duplicate_username(client):
    register(client)
    r = register(client, email="lain@institusi.ac.id")
    assert r.status_code == 409 and code(r) == "USERNAME_ALREADY_TAKEN"


def test_register_password_mismatch(client):
    assert register(client, confirm_password="beda12345").status_code == 422


def test_register_short_password(client):
    assert register(client, password="abc", confirm_password="abc").status_code == 422


def test_register_invalid_email(client):
    assert register(client, email="bukan-email").status_code == 422


def test_register_without_username_ok(client):
    r = client.post("/api/register", json={k: v for k, v in REGISTER.items() if k != "username"})
    # username kosong -> dibuat otomatis dari email (halaman Profil frontend menampilkan @username)
    assert r.status_code == 201 and r.json()["username"] == "peneliti"


# ---------- login ----------
def test_login_by_email_returns_token(client):
    register(client)
    r = login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 3600
    assert body["user"]["full_name"] == "Dr. John Doe"


def test_login_by_username(client):
    register(client)
    assert login(client, username="JohnDoe").status_code == 200


def test_login_wrong_password(client):
    register(client)
    r = login(client, password="salah-salah")
    assert r.status_code == 401 and code(r) == "INVALID_CREDENTIALS"
    assert r.headers["www-authenticate"] == "Bearer"


def test_login_unknown_user_same_response_as_wrong_password(client):
    register(client)
    wrong_pw = login(client, password="salah-salah")
    unknown = login(client, username="tidak-ada@institusi.ac.id")
    assert unknown.status_code == 401
    assert unknown.json() == wrong_pw.json()  # tidak membedakan "akun tidak ada" vs "password salah"


def test_login_overlong_password_rejected(client):
    register(client)
    assert login(client, password="x" * 500).status_code == 401


# ---------- token / endpoint terlindungi ----------
def test_me_with_valid_token(client):
    register(client)
    token = login(client).json()["access_token"]
    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["email"] == "peneliti@institusi.ac.id"


def test_me_without_token(client):
    r = client.get("/api/me")
    assert r.status_code == 401 and code(r) == "TOKEN_MISSING"


def test_me_with_expired_token(client):
    uid = register(client).json()["user_id"]
    token, _ = create_access_token(uid, expires_delta=timedelta(seconds=-5))
    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and code(r) == "TOKEN_EXPIRED"


def test_me_with_tampered_token(client):
    register(client)
    token = login(client).json()["access_token"]
    r = client.get("/api/me", headers={"Authorization": f"Bearer {token[:-3]}abc"})
    assert r.status_code == 401 and code(r) == "TOKEN_INVALID"


def test_me_with_garbage_token(client):
    r = client.get("/api/me", headers={"Authorization": "Bearer bukan.token.jwt"})
    assert r.status_code == 401 and code(r) == "TOKEN_INVALID"


def test_token_for_deleted_user_rejected(client):
    token, _ = create_access_token(9999)
    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and code(r) == "TOKEN_INVALID"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["database"] == "connected"


# ---------- format error validasi (422) ----------
def test_validation_error_uses_standard_shape_and_indonesian(client):
    r = register(client, full_name="A", email="bukan-email", password="abc", confirm_password="abc")
    assert r.status_code == 422 and code(r) == "VALIDATION_ERROR"
    errors = {e["field"]: e["message"] for e in r.json()["detail"]["errors"]}
    assert errors["full_name"] == "Nama lengkap minimal 2 karakter."
    assert errors["email"] == "Format email tidak valid."
    assert errors["password"] == "Kata sandi minimal 8 karakter."
    assert "confirm_password" not in errors  # tidak ada error kedua akibat password yang sudah gagal


def test_password_mismatch_is_reported_on_confirm_password_field(client):
    r = register(client, confirm_password="beda12345")
    errors = {e["field"]: e["message"] for e in r.json()["detail"]["errors"]}
    assert errors == {"confirm_password": "Konfirmasi kata sandi tidak sama dengan kata sandi."}


def test_validation_error_never_echoes_submitted_password(client):
    r = register(client, password="RahasiaBanget1", confirm_password="BedaBanget123")
    assert "RahasiaBanget1" not in r.text and "BedaBanget123" not in r.text
    r = register(client, password="abc", confirm_password="abc")
    assert "abc" not in [e.get("input") for e in r.json()["detail"]["errors"]]
    assert "input" not in r.text


def test_login_missing_fields_uses_standard_shape(client):
    r = client.post("/api/login", data={"username": "akmal"})
    assert r.status_code == 422 and code(r) == "VALIDATION_ERROR"
    assert r.json()["detail"]["errors"] == [{"field": "password", "message": "Kata sandi wajib diisi."}]
    r = client.post("/api/login", data={"password": "x"})
    assert r.json()["detail"]["errors"] == [{"field": "username", "message": "Email / Username wajib diisi."}]


def test_register_invalid_username_characters(client):
    r = register(client, username="a b!")
    errors = {e["field"]: e["message"] for e in r.json()["detail"]["errors"]}
    assert "username" in errors and "huruf, angka" in errors["username"]


# ---------- kepemilikan data (SKPL BR-06) ----------
def _user(uid):
    return User(user_id=uid, full_name="x", email=f"{uid}@x.id", password_hash="h")


def test_ensure_owner_allows_owner():
    ensure_owner(7, _user(7))  # tidak melempar


@pytest.mark.parametrize("owner_id", [8, None])
def test_ensure_owner_hides_other_users_data_as_404(owner_id):
    with pytest.raises(APIError) as exc:
        ensure_owner(owner_id, _user(7))
    assert exc.value.status_code == 404 and exc.value.detail["code"] == "RESOURCE_NOT_FOUND"


# ---------- lupa & reset kata sandi ----------
from datetime import timedelta as _td  # noqa: E402

from app.security import create_reset_token  # noqa: E402


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr("app.routers.auth.send_password_reset_email", lambda to, link: sent.append((to, link)))
    return sent


def token_from(link):
    return link.split("token=")[1]


def forgot(client, email="peneliti@institusi.ac.id"):
    return client.post("/api/forgot-password", json={"email": email})


def reset(client, token, password="sandibaru123", confirm=None):
    return client.post(
        "/api/reset-password",
        json={"token": token, "password": password, "confirm_password": confirm or password},
    )


def test_forgot_password_same_response_for_known_and_unknown_email(client, outbox):
    register(client)
    known, unknown = forgot(client), forgot(client, "tidak-ada@institusi.ac.id")
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert [to for to, _ in outbox] == ["peneliti@institusi.ac.id"]  # email hanya terkirim ke akun yang ada
    assert outbox[0][1].startswith("http://localhost:3000/reset-password?token=")


def test_forgot_password_invalid_email_is_validation_error(client, outbox):
    r = forgot(client, "bukan-email")
    assert r.status_code == 422 and code(r) == "VALIDATION_ERROR" and outbox == []


def test_reset_password_full_flow(client, outbox):
    register(client)
    forgot(client)
    r = reset(client, token_from(outbox[0][1]))
    assert r.status_code == 200
    assert login(client, password="rahasia123").status_code == 401  # sandi lama tidak berlaku
    assert login(client, password="sandibaru123").status_code == 200


def test_reset_token_is_single_use(client, outbox):
    register(client)
    forgot(client)
    token = token_from(outbox[0][1])
    assert reset(client, token).status_code == 200
    again = reset(client, token, password="sandilain1234")
    assert again.status_code == 400 and code(again) == "RESET_TOKEN_INVALID"
    assert login(client, password="sandibaru123").status_code == 200  # sandi tidak berubah lagi


def test_reset_token_expired(client):
    uid = register(client).json()["user_id"]
    from app.database import get_db
    from app.main import app
    db = next(app.dependency_overrides[get_db]())
    token = create_reset_token(uid, db.get(User, uid).password_hash, _td(seconds=-5))
    r = reset(client, token)
    assert r.status_code == 400 and code(r) == "RESET_TOKEN_EXPIRED"


def test_reset_rejects_garbage_and_access_tokens(client):
    register(client)
    access = login(client).json()["access_token"]
    for bad in ("bukan.token.jwt-sama-sekali", access):
        r = reset(client, bad)
        assert r.status_code == 400 and code(r) == "RESET_TOKEN_INVALID"


def test_reset_token_cannot_be_used_as_login_token(client, outbox):
    register(client)
    forgot(client)
    r = client.get("/api/me", headers={"Authorization": f"Bearer {token_from(outbox[0][1])}"})
    assert r.status_code == 401 and code(r) == "TOKEN_INVALID"


def test_reset_password_validation(client, outbox):
    register(client)
    forgot(client)
    token = token_from(outbox[0][1])
    r = reset(client, token, password="sandibaru123", confirm="beda1234567")
    assert r.status_code == 422
    assert r.json()["detail"]["errors"] == [
        {"field": "confirm_password", "message": "Konfirmasi kata sandi tidak sama dengan kata sandi."}
    ]
    assert reset(client, token, password="pendek", confirm="pendek").status_code == 422
    assert login(client, password="rahasia123").status_code == 200  # tidak ada yang berubah


def test_inactive_account_gets_no_reset_email(client, outbox):
    from app.database import get_db
    from app.main import app
    uid = register(client).json()["user_id"]
    db = next(app.dependency_overrides[get_db]())
    db.get(User, uid).is_active = False
    db.commit()
    assert forgot(client).status_code == 202 and outbox == []
