"""Reading and writing mail for agent email: parsing what arrives, deciding
who it is for and whether its sender can be trusted, and the IMAP / SMTP
calls (blocking; the service runs them in a thread).
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parseaddr

MAX_TEXT_ATTACHMENT = 20_000
MAX_BODY = 30_000
MAX_FETCH = 20
TIMEOUT = 30


class MailError(RuntimeError):
    """The mail server refused or could not be reached; safe to show."""


class MailAuthError(MailError):
    """The server rejected the sign-in: retrying won't help."""


# --- addresses -------------------------------------------------------------

_NAME_RE = re.compile(r'^[a-z0-9](?:[a-z0-9._-]{0,38}[a-z0-9])?$')


def valid_address(value: str) -> bool:
    """``local@domain.tld``, checked without a regular expression (no slow
    backtracking on long hostile input)."""
    value = value or ''
    if len(value) > 254 or any(c.isspace() for c in value) or value.count('@') != 1:
        return False
    local, domain = value.split('@')
    labels = domain.split('.')
    return bool(local) and len(labels) >= 2 and all(labels)


def slug(text: str) -> str:
    """An address name from an agent's name: ``Research Bot`` -> ``research-bot``."""
    value = re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-.')
    return value[:40].strip('-.') or 'agent'


def valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or '')) and '..' not in name


def split(address: str) -> tuple[str, str]:
    local, _, domain = (address or '').strip().lower().rpartition('@')
    return local, domain


def agent_address(base: str, addressing: str, name: str) -> str:
    local, domain = split(base)
    if addressing == 'domain':
        return f'{name}@{domain}'
    return f'{local.split("+", 1)[0]}+{name}@{domain}'


def name_for(base: str, addressing: str, recipient: str) -> str | None:
    """Which agent name ``recipient`` addresses in this mailbox: '' for the
    mailbox's own address, None when it isn't one of its addresses at all."""
    local, domain = split(base)
    r_local, r_domain = split(recipient)
    if not r_local or r_domain != domain:
        return None
    root = local.split('+', 1)[0]
    if r_local == local or r_local == root:
        return ''
    if addressing == 'plus':
        head, plus, tag = r_local.partition('+')
        return tag if plus and head == root and tag else None
    return r_local


# --- incoming --------------------------------------------------------------

@dataclass
class Attachment:
    name: str
    content_type: str
    size: int
    text: str = ''
    """The content, for a small text attachment."""


@dataclass
class Inbound:
    message_id: str
    sender: str
    sender_name: str
    subject: str
    body: str
    recipients: list[str]
    in_reply_to: str = ''
    references: list[str] = field(default_factory=list)
    auth_results: str = ''
    automated: bool = False
    """A bounce, an auto-reply or list mail: never answered (no mail loops)."""
    attachments: list[Attachment] = field(default_factory=list)

    def thread_ids(self) -> list[str]:
        ids = [self.in_reply_to, *reversed(self.references)]
        return [i for i in dict.fromkeys(ids) if i]


_MSGID_RE = re.compile(r'<[^<>\s]+>')


def _ids(value: str | None) -> list[str]:
    return _MSGID_RE.findall(str(value or ''))


def _html_text(html: str) -> str:
    text = re.sub(r'(?is)<(script|style).*?</\1>', '', html)
    text = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    from html import unescape

    return re.sub(r'\n{3,}', '\n\n', unescape(text)).strip()


def parse(raw: bytes) -> Inbound:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, sender = parseaddr(str(msg.get('From', '')))
    recipients: list[str] = []
    for header in ('Delivered-To', 'X-Original-To', 'Envelope-To', 'To', 'Cc'):
        for value in msg.get_all(header) or []:
            recipients += [a.lower() for _n, a in getaddresses([str(value)]) if a]
    plain, html = '', ''
    attachments: list[Attachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        filename = part.get_filename()
        if filename or part.get_content_disposition() == 'attachment':
            payload = part.get_payload(decode=True) or b''
            text = ''
            if ctype.startswith('text/') or ctype in ('application/json', 'application/xml'):
                if len(payload) <= MAX_TEXT_ATTACHMENT:
                    text = payload.decode(part.get_content_charset() or 'utf-8', 'replace')
            attachments.append(Attachment(filename or 'attachment', ctype, len(payload), text))
            continue
        try:
            content = part.get_content()
        except (LookupError, ValueError):
            content = (part.get_payload(decode=True) or b'').decode('utf-8', 'replace')
        if ctype == 'text/plain' and not plain:
            plain = str(content)
        elif ctype == 'text/html' and not html:
            html = str(content)
    body = plain or _html_text(html)
    auto = str(msg.get('Auto-Submitted', 'no')).strip().lower()
    precedence = str(msg.get('Precedence', '')).strip().lower()
    local = sender.lower().split('@')[0]
    automated = (
        (auto and auto != 'no')
        or precedence in ('bulk', 'list', 'junk', 'auto_reply')
        or bool(msg.get('List-Id')) or bool(msg.get('X-Autoreply')) or bool(msg.get('X-Autorespond'))
        or local in ('mailer-daemon', 'postmaster') or local.startswith(('noreply', 'no-reply', 'donotreply'))
        or not sender
    )
    in_reply_to = (_ids(msg.get('In-Reply-To')) or [''])[0]
    return Inbound(
        message_id=(_ids(msg.get('Message-ID')) or [''])[0],
        sender=sender.lower(),
        sender_name=name,
        subject=str(msg.get('Subject', '') or '').strip(),
        body=body[:MAX_BODY],
        recipients=list(dict.fromkeys(recipients)),
        in_reply_to=in_reply_to,
        references=_ids(msg.get('References')),
        # The receiving server adds its own header on top; a sender can put
        # fake ones further down, so only the first one counts.
        auth_results=str((msg.get_all('Authentication-Results') or [''])[0]),
        automated=automated,
        attachments=attachments,
    )


_QUOTE_HEADS = (
    re.compile(r'^On .{0,300}wrote:\s*$', re.M | re.S),
    re.compile(r'^-{2,}\s*Original Message\s*-{2,}', re.M | re.I),
    re.compile(r'^_{10,}\s*$', re.M),
    re.compile(r'^From: .+\n(Sent|Date): ', re.M),
    re.compile(r'^Le .{0,300}a écrit\s*:\s*$', re.M | re.S),
    re.compile(r'^El .{0,300}escribió:\s*$', re.M | re.S),
)


def new_text(body: str) -> str:
    """What the sender wrote, without the quoted earlier messages below it."""
    text = (body or '').replace('\r\n', '\n')
    cut = len(text)
    for pattern in _QUOTE_HEADS:
        match = pattern.search(text)
        if match and match.start() > 0:
            cut = min(cut, match.start())
    text = text[:cut]
    lines = text.rstrip().split('\n')
    while lines and (lines[-1].startswith('>') or not lines[-1].strip()):
        lines.pop()
    text = '\n'.join(lines).strip()
    # A "-- " line starts the signature.
    head, sep, _sig = text.partition('\n-- \n')
    return (head if sep else text).strip()


def _domain_of(value: str) -> str:
    return value.strip().strip('<>').lower().rpartition('@')[2].strip('.')


def _aligned(found: str, domain: str) -> bool:
    found = found.strip('.').lower()
    return bool(found) and (domain == found or domain.endswith('.' + found) or found.endswith('.' + domain))


def authenticated(auth_results: str, sender: str) -> bool:
    """The receiving server says the mail really comes from the sender's
    domain: DMARC passed, or DKIM / SPF passed for that same domain."""
    domain = _domain_of(sender)
    if not domain or not auth_results:
        return False
    text = auth_results.lower()
    for clause in re.split(r';', text):
        clause = clause.strip()
        method = re.match(r'(dmarc|dkim|spf)\s*=\s*(\w+)', clause)
        if not method or method.group(2) != 'pass':
            continue
        kind = method.group(1)
        if kind == 'dmarc':
            frm = re.search(r'header\.from\s*=\s*([^\s;]+)', clause)
            if frm is None or _aligned(_domain_of(frm.group(1)), domain):
                return True
        elif kind == 'dkim':
            sig = re.search(r'header\.(?:d|i)\s*=\s*([^\s;]+)', clause)
            if sig and _aligned(_domain_of(sig.group(1)), domain):
                return True
        else:
            frm = re.search(r'smtp\.mailfrom\s*=\s*([^\s;]+)', clause)
            if frm and _aligned(_domain_of(frm.group(1)), domain):
                return True
    return False


def sender_listed(sender: str, allowed: list[str]) -> bool:
    sender = sender.strip().lower()
    domain = _domain_of(sender)
    for entry in allowed:
        entry = entry.strip().lower()
        if not entry:
            continue
        if entry.startswith('@'):
            if domain == entry[1:]:
                return True
        elif entry == sender:
            return True
    return False


def allowed_list(text: str) -> list[str]:
    return [w for w in re.split(r'[\s,;]+', text or '') if w]


# --- outgoing --------------------------------------------------------------

def reply_subject(subject: str) -> str:
    subject = (subject or '').strip() or '(no subject)'
    return subject if re.match(r'(?i)^(re|aw|sv|antw)\s*:', subject) else f'Re: {subject}'


def build(*, sender: str, sender_name: str, to: str, subject: str, body: str,
          in_reply_to: str = '', references: list[str] | None = None, auto: bool = True) -> EmailMessage:
    message = EmailMessage()
    message['From'] = formataddr((sender_name, sender)) if sender_name else sender
    message['To'] = to
    message['Subject'] = subject
    message['Date'] = formatdate(localtime=False)
    message['Message-ID'] = make_msgid(domain=_domain_of(sender) or None)
    if in_reply_to:
        message['In-Reply-To'] = in_reply_to
        message['References'] = ' '.join([*(references or []), in_reply_to][-20:])
    if auto:
        # Out-of-office replies and other bots don't answer it, so two
        # automated mailboxes can't keep writing to each other.
        message['Auto-Submitted'] = 'auto-replied'
    message.set_content(body or '')
    return message


# --- servers (blocking) ----------------------------------------------------

def _tls() -> ssl.SSLContext:
    return ssl.create_default_context()


def _imap(host: str, port: int, security: str) -> imaplib.IMAP4:
    if security == 'ssl':
        return imaplib.IMAP4_SSL(host, port, ssl_context=_tls(), timeout=TIMEOUT)
    conn = imaplib.IMAP4(host, port, timeout=TIMEOUT)
    if security == 'starttls':
        conn.starttls(ssl_context=_tls())
    return conn


@dataclass
class Fetched:
    uid_validity: str
    last_uid: int
    messages: list[tuple[int, bytes]]


def fetch(*, host: str, port: int, security: str, username: str, password: str,
          folder: str = 'INBOX', after_uid: int = 0, uid_validity: str = '') -> Fetched:
    """New mail since ``after_uid``. When the folder is new to us (or its
    UIDVALIDITY changed) nothing is returned: only mail that arrives from
    now on is read."""
    try:
        conn = _imap(host, port, security)
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        raise MailError(f'Could not reach {host}:{port} ({type(exc).__name__})') from None
    try:
        try:
            conn.login(username, password)
        except imaplib.IMAP4.error as exc:
            raise MailAuthError(f'The mail server rejected the sign-in: {_text(exc)}') from None
        status, data = conn.select(_quote(folder))
        if status != 'OK':
            raise MailError(f'No folder called {folder}')
        validity = _response(conn, 'UIDVALIDITY')
        if not uid_validity or validity != uid_validity:
            status, data = conn.uid('search', None, 'ALL')
            uids = [int(u) for u in (data[0] or b'').split()] if status == 'OK' and data else []
            return Fetched(validity, max(uids, default=0), [])
        status, data = conn.uid('search', None, f'UID {after_uid + 1}:*')
        uids = sorted(int(u) for u in (data[0] or b'').split() if int(u) > after_uid) if status == 'OK' and data else []
        messages: list[tuple[int, bytes]] = []
        for uid in uids[:MAX_FETCH]:
            status, parts = conn.uid('fetch', str(uid), '(RFC822)')
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
            if status == 'OK' and raw:
                messages.append((uid, bytes(raw)))
        return Fetched(validity, uids[min(len(uids), MAX_FETCH) - 1] if uids else after_uid, messages)
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        raise MailError(f'Reading mail failed: {_text(exc)}') from None
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _quote(folder: str) -> str:
    folder = folder or 'INBOX'
    return f'"{folder}"' if ' ' in folder and not folder.startswith('"') else folder


def _response(conn: imaplib.IMAP4, name: str) -> str:
    try:
        _typ, data = conn.response(name)
        return (data[0] or b'').decode() if data and data[0] else ''
    except Exception:
        return ''


def _text(exc: BaseException) -> str:
    value = exc.args[0] if exc.args else exc
    return value.decode('utf-8', 'replace') if isinstance(value, bytes) else str(value)


def check_imap(**kw) -> None:
    fetch(**kw, after_uid=0, uid_validity='')


def send(message: EmailMessage, *, host: str, port: int, security: str, username: str, password: str) -> None:
    try:
        if security == 'ssl':
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=_tls())
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        with server:
            if security == 'starttls':
                server.starttls(context=_tls())
            if username:
                server.login(username, password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailAuthError(f'The mail server rejected the sign-in for sending: {exc.smtp_code}') from None
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise MailError(f'Sending failed: {exc}') from None


def check_smtp(*, host: str, port: int, security: str, username: str, password: str) -> None:
    try:
        if security == 'ssl':
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=_tls())
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        with server:
            if security == 'starttls':
                server.starttls(context=_tls())
            if username:
                server.login(username, password)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailAuthError(f'The mail server rejected the sign-in for sending: {exc.smtp_code}') from None
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise MailError(f'Could not reach the sending server: {exc}') from None
