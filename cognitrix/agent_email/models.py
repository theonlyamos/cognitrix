"""Agent email: a mailbox a person connects so each of their agents gets its
own address. Mail sent to an agent's address starts a chat with that agent,
and the agent answers from the same address.

One ``EmailAccount`` per person (their mailbox: IMAP to read, SMTP to send),
one ``AgentAddress`` per agent that has an address, and one ``EmailLink`` per
message seen or sent, so a reply in the same thread continues the same chat.

The mailbox password is stored sealed (``common/secret_box``) and never sent
back by the API.

Persistence rule (as elsewhere): instance ``save()`` once at creation,
``update_one`` after.
"""
from __future__ import annotations

from odbms import Model
from pydantic import field_validator

ADDRESSING = ('plus', 'domain')
"""'plus': agents get ``box+name@domain`` (works with any mailbox, Gmail included).
'domain': agents get ``name@domain`` (the domain's mail all lands in this mailbox)."""

SECURITY = ('ssl', 'starttls', 'none')


class EmailAccount(Model):
    user_id: str
    """Principal key of the person who owns the mailbox."""
    address: str
    """The mailbox's own address, e.g. ``agents@example.com``."""
    addressing: str = 'plus'
    imap_host: str = ''
    imap_port: int = 993
    imap_security: str = 'ssl'
    smtp_host: str = ''
    smtp_port: int = 465
    smtp_security: str = 'ssl'
    username: str = ''
    """Sign-in name for both servers ('' means the address)."""
    password: str = ''
    """Sealed with secret_box."""
    folder: str = 'INBOX'

    agent_id: str = ''
    """Agent that answers mail sent to the mailbox's own address. '' = the person's first agent."""
    allowed_senders: str = ''
    """Who may give the agents work by email, one per line: addresses or ``@domain``.
    '' means only the person's own account email."""
    require_auth: bool = True
    """Only trust a sender whose mail passed SPF, DKIM or DMARC for their own
    domain, as the receiving server reports it (Authentication-Results)."""
    answer_others: bool = False
    """Mail from anyone else gets a read-only answer instead of being ignored."""
    enabled: bool = True

    uid_validity: str = ''
    last_uid: int = 0
    """Highest IMAP UID already handled; older mail is never read again."""
    status: str = ''
    """'' (stopped), 'connecting', 'connected' or 'error'."""
    last_error: str = ''
    last_checked_at: str = ''

    @field_validator('enabled', 'require_auth', mode='before')
    def _null_true(cls, value):
        return True if value is None else value

    @field_validator('answer_others', mode='before')
    def _null_false(cls, value):
        return bool(value)

    @field_validator('last_uid', 'imap_port', 'smtp_port', mode='before')
    def _null_int(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0


class AgentAddress(Model):
    """The address of one agent: ``name`` is the part that tells agents apart
    (``box+name@domain`` or ``name@domain``)."""

    account_id: str
    user_id: str
    agent_id: str
    name: str
    enabled: bool = True

    @field_validator('enabled', mode='before')
    def _null_true(cls, value):
        return True if value is None else value


class EmailLink(Model):
    """A Message-ID seen or sent, and the chat it belongs to."""

    account_id: str
    message_id: str
    session_id: str
    agent_id: str
    guest: bool = False
    """The thread was started by someone who isn't an allowed sender."""
    subject: str = ''
    peer: str = ''
    """The other side's address, where replies go."""

    @field_validator('guest', mode='before')
    def _null_guest(cls, value):
        return bool(value)


MODELS = (EmailAccount, AgentAddress, EmailLink)
