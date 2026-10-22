"""
Sends e-mail through any ordinary SMTP account (for example a Gmail account
with an "app password"). Settings come from .env:

    MAIL_SERVER=smtp.gmail.com
    MAIL_PORT=587
    MAIL_USERNAME=your.address@gmail.com
    MAIL_PASSWORD=your-16-letter-app-password
    MAIL_FROM=SAASSP <your.address@gmail.com>     (optional)

If MAIL_SERVER is not set, nothing is sent and the e-mail text is printed in
the terminal instead (handy while developing on your own computer).
"""
import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app


def mail_configured():
    return bool(current_app.config.get("MAIL_SERVER"))


def send_email(to_address, subject, body):
    """Returns True if the message was handed to the mail server."""
    cfg = current_app.config
    if not cfg.get("MAIL_SERVER"):
        current_app.logger.warning("MAIL_SERVER not set - e-mail NOT sent. Would have sent to %s:\n%s\n%s",
                                   to_address, subject, body)
        print(f"\n--- E-MAIL (not sent, MAIL_SERVER not configured) ---\nTo: {to_address}\n"
              f"Subject: {subject}\n\n{body}\n-----------------------------------------------\n")
        return False

    msg = EmailMessage()
    msg["From"] = cfg.get("MAIL_FROM") or cfg.get("MAIL_USERNAME")
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(cfg.get("MAIL_PORT") or 587)
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(cfg["MAIL_SERVER"], port, timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg["MAIL_SERVER"], port, timeout=15)
            server.starttls(context=ssl.create_default_context())
        with server:
            if cfg.get("MAIL_USERNAME"):
                server.login(cfg["MAIL_USERNAME"], cfg.get("MAIL_PASSWORD", ""))
            server.send_message(msg)
        return True
    except Exception:
        current_app.logger.exception("Could not send e-mail to %s", to_address)
        return False
