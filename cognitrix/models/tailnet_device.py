"""A machine on the owner's tailnet that agents may run commands on
(cognitrix/tailscale/devices.py, Settings > Tailscale).

Server-wide: the tailnet is the admin's, so only an admin adds or changes a
row. A machine with no row can't be used at all.

Persistence rule: ``save()`` once when the device is first allowed; later
writes are partial ``update_one`` calls.
"""
from odbms import Model
from pydantic import validator


class TailnetDevice(Model):
    node: str
    """Tailscale's own id for the machine, which a rename doesn't change."""
    name: str = ''
    """Its name when it was allowed, shown while it is off the tailnet."""
    users: list[str] = []
    """Login names agents may run commands as on it."""
    path: str = 'tailscale'
    """'tailscale' (Tailscale SSH) or 'key' (ordinary SSH with this server's own key)."""
    added_by: str = ''
    created_at: str = ''

    @validator('users', pre=True)
    def _coerce_null_list(cls, value):
        return [] if value is None else value


MODELS = (TailnetDevice,)
