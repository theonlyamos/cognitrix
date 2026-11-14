from odbms import Model


class User(Model):
    name: str
    email: str
    password: str
    tokens_valid_after: int | None = None
    """Unix time; session tokens issued before it are rejected (set on logout)."""

    onboarded_at: int | None = None
    """Unix time the user finished (or skipped) first-run setup of their assistant."""

    role: str | None = None
    """admin or member. None keeps the older rule: the first account (or the
    emails in COGNITRIX_ADMIN_EMAILS) is the admin."""

    disabled: bool | None = None
    """A disabled account cannot sign in and its sessions and keys stop working."""

    reset_token_hash: str | None = None
    """SHA-256 of a pending password-reset token, if one was emailed."""

    reset_expires: int | None = None
    """Unix time the reset token stops working."""

    whats_new_seen: str | None = None
    """Last app version whose What's new notes this user saw (or skipped)."""
