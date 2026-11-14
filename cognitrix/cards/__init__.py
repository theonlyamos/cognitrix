"""Agent cards: small structured layouts an agent shows in the chat.

The agent writes a card with the Show Card tool (``cognitrix/tools/cards.py``)
as a tree of typed elements: fields, a table, a chart, buttons. The server
checks it here and stores the cleaned card on the tool outcome, so the chat
draws it (``frontend/src/components/cards``) and chat apps get a plain text
version (``to_markdown``) with buttons.

The element names and the flat ``{"root", "elements"}`` form follow
json-render (Apache-2.0), so specs written for it mostly work here.
"""
from cognitrix.cards.spec import (
    CardError,
    button_message,
    buttons_of,
    normalize,
    to_markdown,
)

__all__ = ['CardError', 'button_message', 'buttons_of', 'normalize', 'to_markdown']
