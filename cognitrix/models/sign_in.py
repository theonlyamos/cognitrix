"""Signed-in devices and one-time sign-in codes (cognitrix/signins.py).

A ``SignIn`` row is one device's session. Its id travels in the session
token as ``sid``, so one device can be signed out without the others.

A ``SignInCode`` is made on a signed-in device (Settings, Sign-ins, Sign in
another device) and typed on a new one. Only the sha256 of the code is kept.

Persistence rule: ``save()`` once, later writes are partial ``update_one``.
"""
from odbms import Model


class SignIn(Model):
    sid: str
    user_id: str
    name: str = ''
    """What the device is, e.g. 'Chrome on Windows' or what the app sent."""
    kind: str = 'browser'
    """'browser' | 'desktop' | 'cli'."""
    user_agent: str = ''
    address: str = ''
    method: str = 'password'
    """'password' | 'google' | 'github' | 'code' | 'signup' | 'earlier'."""
    approved_by: str = ''
    """For method 'code': the name of the device that made the code."""
    approved_by_sid: str = ''
    created_at: str = ''
    last_seen_at: str = ''
    """Naive UTC 'YYYY-MM-DD HH:MM:SS'."""
    ended_at: str = ''
    """Set when the device was signed out. An ended row no longer signs anything in."""


class SignInCode(Model):
    code_hash: str
    user_id: str
    made_by_sid: str = ''
    made_by_name: str = ''
    expires_at: int = 0
    used_at: int = 0
    used_sid: str = ''
    """The SignIn the code made, once it was used."""
    canceled: bool = False


MODELS = (SignIn, SignInCode)
