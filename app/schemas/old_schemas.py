"""Bentuk data masuk dan keluar untuk modul akun (register, login, profil, pemulihan kata sandi)."""
from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, ValidationInfo, field_validator


class _PasswordPair(BaseModel):
    # AliasChoices membuat API menerima nama kolom snake_case (dokumentasi, Swagger) dan camelCase
    # (state fullName dan confirmPassword di frontend), jadi frontend tidak perlu mengganti nama kolom.
    """Dasar untuk isian yang memuat kata sandi dan konfirmasinya. Memeriksa keduanya sama."""
    model_config = ConfigDict(populate_by_name=True)

    password: str = Field(min_length=8, max_length=128, description="Kata sandi, 8 sampai 128 karakter.")
    confirm_password: str = Field(validation_alias=AliasChoices("confirm_password", "confirmPassword"),
                                  description="Harus sama persis dengan password.")

    @field_validator("confirm_password")
    @classmethod
    def passwords_must_match(cls, value: str, info: ValidationInfo) -> str:
        # Kesalahan ditautkan ke kolom confirm_password agar frontend menandai kolom yang tepat.
        # Bila password sendiri sudah gagal validasi, kesalahan kedua tidak perlu ditambahkan.
        """Memastikan konfirmasi sama dengan kata sandi. Kesalahan ditautkan ke kolom confirm_password."""
        if "password" in info.data and value != info.data["password"]:
            raise ValueError("Konfirmasi kata sandi tidak sama dengan kata sandi.")
        return value


class RegisterRequest(_PasswordPair):
    """Isian pendaftaran akun."""
    full_name: str = Field(min_length=2, max_length=100, validation_alias=AliasChoices("full_name", "fullName"))
    email: EmailStr
    # Form pendaftaran frontend tidak punya kolom username. Bila kosong, backend membuatnya dari email.
    username: str | None = Field(default=None, min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    institution: str | None = Field(default=None, max_length=150)
    bio: str | None = Field(default=None, max_length=500)


class LoginRequest(BaseModel):
    """Isi login dalam bentuk JSON. Kolom `identifier` boleh berisi email atau username."""

    model_config = ConfigDict(populate_by_name=True, json_schema_extra={
        "example": {"identifier": "akmal.f@apps.ipb.ac.id", "password": "kata-sandi-anda"}})

    identifier: str | None = Field(default=None, validation_alias=AliasChoices("identifier", "email", "username"),
                                   description="Email atau username.")
    password: str


class ProfileUpdate(BaseModel):
    """Isi PATCH /api/me. Hanya kolom yang dikirim yang diubah. Email, username, dan kata sandi tidak lewat jalur ini."""

    model_config = ConfigDict(populate_by_name=True)

    full_name: str | None = Field(default=None, min_length=2, max_length=100, validation_alias=AliasChoices("full_name", "fullName"))
    institution: str | None = Field(default=None, max_length=150)
    bio: str | None = Field(default=None, max_length=500)


class ForgotPasswordRequest(BaseModel):
    """Isian permintaan tautan pemulihan kata sandi."""
    email: EmailStr


class ResetPasswordRequest(_PasswordPair):
    """Isian pengaturan ulang kata sandi: token dari tautan dan kata sandi baru."""
    token: str = Field(min_length=10, max_length=2000)


class MessageOut(BaseModel):
    """Jawaban berisi satu pesan singkat."""
    message: str


class UserOut(BaseModel):
    """Data akun yang boleh dikirim ke frontend. Kata sandi tidak pernah ikut."""
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    full_name: str
    email: EmailStr
    username: str | None
    institution: str | None = None
    bio: str | None = None
    created_at: datetime


class TokenOut(BaseModel):
    """Respons login. Frontend wajib menyimpan `access_token` lalu mengirimnya pada setiap permintaan berikutnya
    lewat header `Authorization: Bearer <access_token>`."""

    access_token: str = Field(description="Token yang dikirim pada header Authorization di setiap permintaan yang butuh login.")
    token_type: str = Field("bearer", description="Selalu bearer.")
    expires_in: int = Field(description="Masa berlaku token dalam detik. Setelah itu pengguna harus login lagi.")
    user: UserOut
