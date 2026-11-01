"""Per-user voice input settings: which speech-to-text provider and model to
use, plus API keys the user pasted for providers the server has no key for.

Kept out of the ``User`` row on purpose: login returns ``user.json()``, and the
keys must never go back to the browser.

Persistence rule: instance ``save()`` once at creation, ``update_one`` after.
"""
from odbms import Model


class VoiceSettings(Model):
    user_id: str
    provider: str = ''
    model: str = ''
    api_keys: str = ''
    """JSON object of provider id -> API key the user pasted."""
