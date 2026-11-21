"""Dependency bersama untuk endpoint: pemeriksaan token login dan pengecekan kepemilikan data."""
import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import APIError
from app.models import User
from app.security import decode_access_token

# auto_error=False supaya format kesalahan kita sendiri yang dipakai, bukan pesan bawaan "Not authenticated".
# tokenUrl membuat tombol "Authorize" di Swagger UI memanggil /api/login.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


def get_current_user(token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    """Pelindung endpoint: memeriksa token di header Authorization lalu mengembalikan akun pemiliknya.
    Cara pakai: `user: User = Depends(get_current_user)`. Tanpa token yang sah, jawabannya 401."""
    if not token:
        raise APIError(401, "TOKEN_MISSING", "Anda belum login. Silakan masuk terlebih dahulu.", bearer=True)
    try:
        user_id = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise APIError(401, "TOKEN_EXPIRED", "Sesi Anda telah berakhir. Silakan login kembali.", bearer=True)
    except jwt.InvalidTokenError:
        raise APIError(401, "TOKEN_INVALID", "Token tidak valid. Silakan login kembali.", bearer=True)

    user = db.get(User, user_id)
    if user is None:
        raise APIError(401, "TOKEN_INVALID", "Token tidak valid. Silakan login kembali.", bearer=True)
    if not user.is_active:
        raise APIError(403, "USER_INACTIVE", "Akun Anda dinonaktifkan. Hubungi administrator.")
    return user


def ensure_owner(owner_id: int | None, user: User) -> None:
    """SKPL BR-06: pengguna hanya boleh mengakses analisis dan hasil milik akunnya sendiri.

    Panggil setelah mengambil baris dari database, misalnya `ensure_owner(analysis.user_id, user)`.
    Jawabannya 404 (bukan 403) supaya keberadaan data milik orang lain tidak terbongkar.
    """
    if owner_id is None or owner_id != user.user_id:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Data tidak ditemukan.")
