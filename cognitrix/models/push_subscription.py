"""A browser that agreed to receive push notifications for one person.

``p256dh`` and ``auth`` are the keys the browser made for encrypting messages
to it; they are not secrets of ours, but they are never returned by the API.
"""
from odbms import Model


class PushSubscription(Model):
    user_id: str
    endpoint: str
    p256dh: str
    auth: str
    label: str = ''
    """What the person sees in the list, for example 'Chrome on Android'."""
