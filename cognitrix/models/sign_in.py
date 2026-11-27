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
    """'password' | 'google' | 'github' | 'code' | 'passkey' | 'tailscale' | 'signup' | 'earlier'."""
    approved_by: str = ''
    """For method 'code': the name of the device that made the code. For 'passkey': the passkey's name.
    For 'tailscale': the Tailscale login."""
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


class Passkey(Model):
    """A passkey on someone's account (cognitrix/passkeys.py). Only its public key is kept."""
    passkey_id: str
    user_id: str
    credential_id: str
    """base64url, as the browser reports it in ``rawId``."""
    public_key: str
    """COSE public key, base64url."""
    sign_count: int = 0
    rp_id: str = ''
    """The host name it was made on; it only signs in there."""
    name: str = ''
    provider: str = ''
    """'Windows Hello', 'iCloud Keychain', ... when the AAGUID is a known one."""
    aaguid: str = ''
    backed_up: bool = False
    """Synced to the person's other devices (a password manager or cloud keychain)."""
    transports: str = ''
    created_at: str = ''
    last_used_at: str = ''


class PasskeyCeremony(Model):
    """One passkey prompt's challenge. Used once, within a few minutes."""
    ceremony_id: str
    kind: str
    """'register' | 'sign_in'."""
    challenge: str
    rp_id: str = ''
    origin: str = ''
    user_id: str = ''
    handoff_id: str = ''
    name: str = ''
    expires_at: int = 0
    used: bool = False


class PasskeyHandoff(Model):
    """The desktop app doing a passkey step in the default browser (its webview can't).

    The app keeps the secret (only its sha256 is here); the browser page only
    knows ``handoff_id``.
    """
    handoff_id: str
    secret_hash: str
    kind: str
    """'sign_in' | 'register'."""
    user_id: str = ''
    made_by_sid: str = ''
    device_name: str = ''
    status: str = 'waiting'
    """'waiting' | 'opened' | 'done' | 'canceled'."""
    sid: str = ''
    """For a finished sign-in: the desktop app's new SignIn."""
    passkey_id: str = ''
    collected: bool = False
    expires_at: int = 0


class TailscaleLink(Model):
    """A Tailscale login someone linked to their account, so opening Cognitrix
    from their tailnet signs them in (cognitrix/tailscale/identity.py). One
    account per login."""
    user_id: str
    login: str
    """Lower case, as Tailscale reports it (``ada@example.com``)."""
    name: str = ''
    created_at: str = ''
    last_used_at: str = ''


MODELS = (SignIn, SignInCode, Passkey, PasskeyCeremony, PasskeyHandoff, TailscaleLink)
