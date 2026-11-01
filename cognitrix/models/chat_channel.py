"""Chat apps (Telegram, Slack) a person connected so they can talk to their
agents and get Inbox alerts and approvals there.

Each person has at most one channel per kind. The bot token belongs to a bot
the person created; nobody else's messages reach their agents, because a
channel only answers the one chat that sent the link code shown in Settings.

Tokens are never returned by the API; the settings only say whether one is set.

Persistence rule: instance ``save()`` once at creation, ``update_one`` after.
"""
from odbms import Model
from pydantic import validator


class ChatChannel(Model):
    user_id: str
    """Principal key of the person who owns the channel."""
    kind: str
    """'telegram' or 'slack'."""
    bot_token: str = ''
    app_token: str = ''
    """Slack only: the app-level token (xapp-...) Socket Mode connects with."""
    bot_name: str = ''
    """The bot's handle as the chat app reports it (Telegram username, Slack bot name)."""
    agent_id: str = ''
    """Agent new conversations use. '' means the person's first agent."""
    enabled: bool = True
    send_alerts: bool = True
    """Forward Notify Me alerts from agents."""
    send_approvals: bool = True
    """Forward task approvals with Approve and Deny buttons."""

    link_code: str = ''
    """One-time code the person sends to the bot to link their chat."""
    link_code_expires_at: str = ''
    chat_id: str = ''
    """The linked chat (Telegram chat id, Slack DM channel id). '' until linked."""
    peer_id: str = ''
    """The linked person's id in the chat app; only their messages are read."""
    peer_name: str = ''
    linked_at: str = ''
    session_id: str = ''
    """The conversation the chat continues; /new starts another."""

    status: str = ''
    """'' (stopped), 'connecting', 'connected' or 'error'."""
    last_error: str = ''

    @validator('enabled', 'send_alerts', 'send_approvals', pre=True)
    def _null_bool(cls, value):
        return True if value is None else value


class ChannelDelivery(Model):
    """One Inbox item already sent to a channel, so it isn't sent twice.

    ``message_ref`` lets the message be edited once the item is settled
    elsewhere (for example an approval answered in the web UI).
    """

    channel_id: str
    item_kind: str
    """'notification' or 'approval'."""
    item_id: str
    message_ref: str = ''
    closed: bool = False

    @validator('closed', pre=True)
    def _null_closed(cls, value):
        return False if value is None else value
