"""A person's own PC paired as a computer their agents can work on
(cognitrix/computer/paired).

Only the sha256 of the PC's token is stored. Folders and powers are what the
PC last reported: shown on the web, but enforced by the PC itself.

Persistence rule: ``save()`` once at pairing; later writes are partial
``update_one`` calls so a heartbeat can't undo a rename.
"""
from odbms import Model


class PairedDevice(Model):
    user_id: str
    name: str
    platform: str = ''
    """'windows' | 'macos' | 'linux' and a version, as the PC reports it."""
    token_hash: str
    folders_json: str = '[]'
    """JSON list of {path, writable}: the folders the PC shares."""
    powers: str = ''
    """Comma separated: 'files', 'commands'."""
    paused: bool = False
    version: str = ''
    """The connector's Cognitrix version."""
    paired_at: str = ''
    last_seen_at: str = ''
    """Naive UTC 'YYYY-MM-DD HH:MM:SS'."""


MODELS = (PairedDevice,)
