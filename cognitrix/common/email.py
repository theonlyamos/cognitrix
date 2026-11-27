"""Sending email over SMTP, for password-reset links.

Configured in Settings, People, Email sending (any SMTP provider works):
COGNITRIX_SMTP_HOST, COGNITRIX_SMTP_PORT (587), COGNITRIX_SMTP_USER,
COGNITRIX_SMTP_PASSWORD, COGNITRIX_SMTP_FROM, COGNITRIX_SMTP_SECURITY
(starttls, ssl or none). COGNITRIX_PUBLIC_URL is the address people open the
app at, used to build links. Without a host, email is off and the app says so.
"""
from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from cognitrix.app_settings import get as app_setting


class EmailError(RuntimeError):
    """Email could not be sent; the message is safe to show to an admin."""


def configured() -> bool:
    return bool(app_setting('COGNITRIX_SMTP_HOST', '').strip())


def public_url() -> str:
    """Where people open this server, for links in emails: the public address
    in Settings, else the Tailscale private address."""
    from cognitrix.common import addresses

    return addresses.people_url()


def _send(to: str, subject: str, body: str) -> None:
    host = app_setting('COGNITRIX_SMTP_HOST', '').strip()
    if not host:
        raise EmailError('Email is not set up on this server')
    security = app_setting('COGNITRIX_SMTP_SECURITY', 'starttls').strip().lower()
    port = int(app_setting('COGNITRIX_SMTP_PORT', '') or (465 if security == 'ssl' else 587))
    user = app_setting('COGNITRIX_SMTP_USER', '').strip()
    password = app_setting('COGNITRIX_SMTP_PASSWORD', '')
    sender = app_setting('COGNITRIX_SMTP_FROM', '').strip() or user
    if not sender:
        raise EmailError('Set "Send as" under Settings, People, Email sending.')

    message = EmailMessage()
    message['From'] = sender
    message['To'] = to
    message['Subject'] = subject
    message.set_content(body)
    try:
        if security == 'ssl':
            server = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if security == 'starttls':
                server.starttls(context=ssl.create_default_context())
            if user:
                server.login(user, password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f'The mail server refused or could not be reached: {exc}') from exc


async def send(to: str, subject: str, body: str) -> None:
    await asyncio.get_running_loop().run_in_executor(None, _send, to, subject, body)
