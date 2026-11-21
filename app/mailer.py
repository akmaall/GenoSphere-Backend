"""Pengiriman email pemulihan kata sandi. Bila SMTP belum diatur (mode pengembangan), tautannya dicetak di log server."""
import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger("genosphere.mailer")


def send_password_reset_email(to_email: str, link: str) -> None:
    """Dijalankan sebagai tugas latar belakang. Kegagalan di sini tidak sampai ke pengguna karena jawaban sudah terkirim lebih dulu."""
    s = get_settings()
    if not s.smtp_host:
        logger.warning("SMTP belum dikonfigurasi (mode pengembangan). Tautan pemulihan untuk %s: %s", to_email, link)
        return

    msg = EmailMessage()
    msg["Subject"] = "Pemulihan Kata Sandi GenoSphere-GWAS"
    msg["From"] = s.smtp_from
    msg["To"] = to_email
    msg.set_content(
        "Kami menerima permintaan untuk mengatur ulang kata sandi akun GenoSphere-GWAS Anda.\n\n"
        f"Buka tautan berikut (berlaku {s.reset_token_expire_minutes} menit, hanya bisa dipakai sekali):\n{link}\n\n"
        "Jika Anda tidak merasa meminta ini, abaikan email ini. Kata sandi Anda tidak berubah."
    )
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password or "")
            smtp.send_message(msg)
    except Exception:
        logger.exception("Gagal mengirim email pemulihan ke %s", to_email)
