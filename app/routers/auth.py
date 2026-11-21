"""Endpoint akun: daftar, masuk, profil, lupa dan atur ulang kata sandi."""
import re
from typing import Annotated

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.errors import APIError, ErrorResponse, build_validation_errors
from app.mailer import send_password_reset_email
from app.models import User
from app.schemas import (ForgotPasswordRequest, LoginRequest, MessageOut, ProfileUpdate, RegisterRequest,
                         ResetPasswordRequest, TokenOut, UserOut)
from app.security import (burn_verify_time, create_access_token, create_reset_token, decode_reset_token,
                          hash_password, reset_token_is_current, verify_password)

router = APIRouter(prefix="/api", tags=["01. Autentikasi & Akun"])

MAX_PASSWORD_LEN = 128

def _suggest_username(db: Session, email: str) -> str:
    base = re.sub(r"[^a-z0-9_.-]", "_", email.split("@")[0].lower()).strip("._-") or "user"
    base = base.ljust(3, "0")[:44]
    candidate, n = base, 1
    while db.scalar(select(User.user_id).where(User.username == candidate)):
        n += 1
        candidate = f"{base}{n}"
    return candidate

@router.post("/register", response_model=UserOut, status_code=201, summary="Daftar akun baru",
             responses={409: {"model": ErrorResponse, "description": "Email atau username sudah dipakai."},
                        422: {"model": ErrorResponse, "description": "Isian belum valid."}})
def register(payload: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> User:
    email = payload.email.lower()
    username = payload.username.lower() if payload.username else None

    if db.scalar(select(User.user_id).where(User.email == email)):
        raise APIError(409, "EMAIL_ALREADY_REGISTERED", "Email sudah terdaftar.")
    if username and db.scalar(select(User.user_id).where(User.username == username)):
        raise APIError(409, "USERNAME_ALREADY_TAKEN", "Username sudah dipakai.")

    user = User(
        full_name=payload.full_name.strip(),
        email=email,
        username=username or _suggest_username(db, email),
        institution=(payload.institution or "").strip() or None,
        bio=(payload.bio or "").strip() or None,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise APIError(409, "ACCOUNT_ALREADY_EXISTS", "Email atau username sudah terdaftar.")
    return user

def _authenticate(db: Session, identifier: str, password: str) -> TokenOut:
    invalid = APIError(401, "INVALID_CREDENTIALS", "Email/username atau kata sandi salah.", bearer=True)

    identifier = identifier.strip().lower()
    user = db.scalar(select(User).where(or_(User.email == identifier, User.username == identifier)))

    if len(password) > MAX_PASSWORD_LEN:
        raise invalid
    if user is None:
        burn_verify_time(password)
        raise invalid
    if not verify_password(password, user.password_hash):
        raise invalid
    if not user.is_active:
        raise APIError(403, "USER_INACTIVE", "Akun Anda dinonaktifkan. Hubungi administrator.")

    token, expires_in = create_access_token(user.user_id)
    return TokenOut(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))

_LOGIN_BODY = {
    "required": True,
    "content": {
        "application/json": {"schema": {
            "type": "object", "required": ["identifier", "password"],
            "properties": {"identifier": {"type": "string", "description": "Email atau username."},
                           "password": {"type": "string"}},
            "example": {"identifier": "user@example.com", "password": "kata-sandi-anda"}}},
        "application/x-www-form-urlencoded": {"schema": {
            "type": "object", "required": ["username", "password"],
            "properties": {"username": {"type": "string", "description": "Email atau username."},
                           "password": {"type": "string"}}}},
    },
}

@router.post("/login", response_model=TokenOut, summary="Masuk (menerima JSON atau form)",
             openapi_extra={"requestBody": _LOGIN_BODY},
             responses={401: {"model": ErrorResponse, "description": "Email/username atau kata sandi salah."},
                        403: {"model": ErrorResponse, "description": "Akun dinonaktifkan."},
                        415: {"model": ErrorResponse, "description": "Jenis isi permintaan bukan JSON maupun form."},
                        422: {"model": ErrorResponse, "description": "Isian belum lengkap."}})
async def login(request: Request, db: Annotated[Session, Depends(get_db)]) -> TokenOut:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    id_field = "identifier"
    if ctype == "application/json":
        try:
            raw = await request.json()
        except ValueError:
            raise APIError(422, "VALIDATION_ERROR", "Isi permintaan bukan JSON yang valid.")
        try:
            data = LoginRequest.model_validate(raw)
        except ValidationError as exc:
            errs = [{**e, "loc": ("body", *e["loc"])} for e in exc.errors()]
            raise APIError(422, "VALIDATION_ERROR", "Data yang dikirim belum valid. Periksa kembali isian Anda.",
                           errors=build_validation_errors(errs))
        identifier, password = data.identifier, data.password
    elif ctype in ("application/x-www-form-urlencoded", "multipart/form-data"):
        form = await request.form()
        identifier, password = form.get("username"), form.get("password")
        id_field = "username"
    else:
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "Kirim data login sebagai JSON (application/json) atau form (application/x-www-form-urlencoded).")

    missing = [{"field": f, "message": f"{label} wajib diisi."}
               for f, label, v in ((id_field, "Email / Username", identifier), ("password", "Kata sandi", password)) if not v]
    if missing:
        raise APIError(422, "VALIDATION_ERROR", "Data yang dikirim belum valid. Periksa kembali isian Anda.", errors=missing)

    return await run_in_threadpool(_authenticate, db, str(identifier), str(password))

@router.post("/login/json", response_model=TokenOut, include_in_schema=False)
async def login_json_alias(payload: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> TokenOut:
    if not payload.identifier:
        raise APIError(422, "VALIDATION_ERROR", "Email / Username wajib diisi.",
                       errors=[{"field": "identifier", "message": "Email / Username wajib diisi."}])
    return await run_in_threadpool(_authenticate, db, payload.identifier, payload.password)

@router.get("/me", response_model=UserOut, summary="Data akun yang sedang login",
            responses={401: {"model": ErrorResponse}})
def me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    return current_user

@router.patch("/me", response_model=UserOut, summary="Ubah nama, institusi, atau bio",
              responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def update_me(
    payload: ProfileUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    data = payload.model_dump(exclude_unset=True)
    if data.get("full_name"):
        current_user.full_name = data["full_name"].strip()
    for key in ("institution", "bio"):
        if key in data:
            setattr(current_user, key, (data[key] or "").strip() or None)
    db.commit()
    db.refresh(current_user)
    return current_user

@router.post("/forgot-password", response_model=MessageOut, status_code=202, summary="Minta tautan pemulihan kata sandi",
             responses={422: {"model": ErrorResponse}})
def forgot_password(payload: ForgotPasswordRequest, background: BackgroundTasks,
                    db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is not None and user.is_active:
        token = create_reset_token(user.user_id, user.password_hash)
        link = f"{get_settings().frontend_url.rstrip('/')}/reset-password?token={token}"
        background.add_task(send_password_reset_email, user.email, link)
    return MessageOut(message="Jika email terdaftar, tautan pemulihan kata sandi telah dikirim.")

@router.post("/reset-password", response_model=MessageOut, summary="Atur kata sandi baru dari tautan pemulihan",
             responses={400: {"model": ErrorResponse, "description": "Tautan tidak valid, sudah dipakai, atau kedaluwarsa."},
                        422: {"model": ErrorResponse}})
def reset_password(payload: ResetPasswordRequest, db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    invalid = APIError(400, "RESET_TOKEN_INVALID", "Tautan pemulihan tidak valid atau sudah pernah dipakai.")
    try:
        user_id, fp = decode_reset_token(payload.token)
    except jwt.ExpiredSignatureError:
        raise APIError(400, "RESET_TOKEN_EXPIRED", "Tautan pemulihan telah kedaluwarsa. Silakan minta tautan baru.")
    except jwt.InvalidTokenError:
        raise invalid

    user = db.get(User, user_id)
    if user is None or not user.is_active or not reset_token_is_current(fp, user.password_hash):
        raise invalid

    user.password_hash = hash_password(payload.password)
    db.commit()
    return MessageOut(message="Kata sandi berhasil diubah. Silakan masuk dengan kata sandi baru.")