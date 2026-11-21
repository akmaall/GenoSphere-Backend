"""Keamanan akun: hash kata sandi (argon2), token login (JWT), dan token pemulihan kata sandi.

Kata sandi tidak pernah disimpan apa adanya. Yang disimpan hanya hash-nya, sehingga isi database yang bocor tidak
langsung membuka kata sandi pengguna.
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.config import get_settings

_hasher = PasswordHash.recommended()
_DUMMY_HASH = _hasher.hash("password-pembanding-agar-waktu-respons-seragam")


def hash_password(plain: str) -> str:
    """Mengubah kata sandi menjadi hash argon2 untuk disimpan."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Memeriksa apakah kata sandi yang diketik cocok dengan hash yang tersimpan."""
    return _hasher.verify(plain, hashed)


def burn_verify_time(plain: str) -> None:
    """Dipanggil saat akun tidak ditemukan, supaya waktu respons tidak membocorkan keberadaan akun."""
    _hasher.verify(plain, _DUMMY_HASH)


def create_access_token(user_id: int, expires_delta: timedelta | None = None) -> tuple[str, int]:
    """Membuat token login. Mengembalikan token dan masa berlakunya dalam detik."""
    s = get_settings()
    delta = expires_delta if expires_delta is not None else timedelta(minutes=s.access_token_expire_minutes)
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "type": "access", "iat": now, "exp": now + delta}
    token = jwt.encode(payload, s.secret_key, algorithm=s.jwt_algorithm)
    return token, int(delta.total_seconds())


def decode_access_token(token: str) -> int:
    """Membaca token login dan mengembalikan user_id. Melempar jwt.ExpiredSignatureError bila kedaluwarsa, atau jwt.InvalidTokenError bila tidak sah."""
    s = get_settings()
    payload = jwt.decode(
        token,
        s.secret_key,
        algorithms=[s.jwt_algorithm],  # daftar eksplisit: mencegah serangan alg=none
        options={"require": ["exp", "iat", "sub"]},
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("bukan access token")
    try:
        return int(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise jwt.InvalidTokenError("sub tidak valid") from exc


# ---- token pemulihan kata sandi -------------------------------------------------------------
# Token ini bertipe "reset" (bukan "access"), jadi tidak bisa dipakai untuk login, dan sebaliknya.
# `fp` adalah sidik jari hash kata sandi saat ini. Begitu kata sandi berubah, token lama otomatis tidak berlaku,
# sehingga token hanya bisa dipakai sekali tanpa perlu tabel tambahan di database.
def _fingerprint(password_hash: str) -> str:
    """Sidik jari singkat dari hash kata sandi. Berubah saat kata sandi diganti, sehingga token pemulihan lama otomatis mati."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def create_reset_token(user_id: int, password_hash: str, expires_delta: timedelta | None = None) -> str:
    """Membuat token untuk tautan pemulihan kata sandi."""
    s = get_settings()
    delta = expires_delta if expires_delta is not None else timedelta(minutes=s.reset_token_expire_minutes)
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "type": "reset", "fp": _fingerprint(password_hash), "iat": now, "exp": now + delta}
    return jwt.encode(payload, s.secret_key, algorithm=s.jwt_algorithm)


def decode_reset_token(token: str) -> tuple[int, str]:
    """Mengembalikan (user_id, fp). Melempar jwt.ExpiredSignatureError / jwt.InvalidTokenError."""
    s = get_settings()
    payload = jwt.decode(token, s.secret_key, algorithms=[s.jwt_algorithm], options={"require": ["exp", "iat", "sub", "fp"]})
    if payload.get("type") != "reset":
        raise jwt.InvalidTokenError("bukan reset token")
    try:
        return int(payload["sub"]), str(payload["fp"])
    except (TypeError, ValueError) as exc:
        raise jwt.InvalidTokenError("sub tidak valid") from exc


def reset_token_is_current(fp: str, password_hash: str) -> bool:
    """True bila token pemulihan masih cocok dengan kata sandi akun saat ini (belum dipakai)."""
    return hmac.compare_digest(fp, _fingerprint(password_hash))
