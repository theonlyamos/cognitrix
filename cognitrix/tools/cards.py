"""Show Card: the agent shows a small structured layout in the chat.

Numbers, a table, a chart and buttons, drawn natively in the web and desktop
app and as text with buttons in chat apps. See ``cognitrix/cards``.

Added per turn to chats and task runs (see ``Session._run_turn``); a button
press comes back to the agent as a message.
"""
from __future__ import annotations

import json
from typing import Any

from cognitrix.cards import CardError, buttons_of, normalize
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome

CARD_TOOL_CATEGORY = 'cards'


@tool(category=CARD_TOOL_CATEGORY, retryable=False, max_attempts=1,
      supported_interfaces=['web', 'ws', 'task'], occupies_execution_slot=False)
async def show_card(card: dict[str, Any] | str, *, parent=None) -> ToolOutcome:
    """Show a card in the chat: key numbers, a small table, a chart and buttons, laid out neatly.
    Use it for structured results the person scans or acts on (a status, a comparison, a report summary,
    choices to pick from). Keep plain answers as text, and use Publish Artifact for a full page.
    After the card, add a sentence or two with the takeaway; don't repeat the card's numbers.

    Short form (parts drawn in the order you write them):
    {"title": "Weekly sales", "subtitle": "Nov 7-13",
     "fields": [{"label": "Revenue", "value": "$48,210", "change": "+12%", "tone": "good"},
                {"label": "Refunds", "value": "1.8%", "change": "+0.6 pt", "tone": "bad"}],
     "chart": {"kind": "bar", "title": "Revenue by day", "unit": "$k",
               "labels": ["Mon", "Tue"], "series": [{"name": "Revenue", "values": [5.1, 6.2]}]},
     "table": {"title": "Top products", "columns": ["Product", "Units"], "rows": [["Linen shirt", 212]]},
     "text": "Markdown text",
     "buttons": [{"label": "Email to team", "action": "email_team", "style": "primary"},
                 {"label": "Open report", "url": "https://..."}]}
    chart and table may also be lists. Chart kind is "bar", "line", "area" or "pie"; data may also be
    [{"label", "value"}]. Field tone is "good", "bad" or "neutral". A button with a url opens it;
    other buttons need an action id (and may carry a "value"). Limits: tables 8 columns and 50 rows,
    4 charts of 40 points, 6 buttons.

    For a custom order or sections, write a tree instead: {"type": "Card", "props": {"title"},
    "children": [{"type": "Fields", "children": [{"type": "Field", "props": {...}}]}, {"type": "Section",
    "props": {"title"}, "children": [...]}, {"type": "Chart", "props": {...}}, {"type": "Table", ...},
    {"type": "Text", ...}, {"type": "Image", "props": {"url", "alt"}}, {"type": "Divider"},
    {"type": "Actions", "children": [{"type": "Button", ...}, {"type": "LinkButton", ...}]}]}.
    json-render's flat {"root", "elements"} form also works.

    When the person presses a Button you get a message like
    [Pressed "Email to team" on card "Weekly sales" (action: email_team)] and should do that.
    Buttons don't do anything by themselves.

    Args:
        card (dict): The card, as described above.
    """
    if isinstance(card, str):
        try:
            card = json.loads(card)
        except ValueError:
            return ToolOutcome.failure('bad_card', 'The card must be a JSON object, not text.')
    try:
        clean = normalize(card)
    except CardError as exc:
        issues = '\n'.join(f'- {issue}' for issue in exc.issues)
        return ToolOutcome.failure('bad_card', f'The card was not shown. Fix these and call Show Card again:\n{issues}')
    buttons = buttons_of(clean)
    what = f'Showed card "{clean["title"]}"' if clean['title'] else 'Showed the card'
    hint = (f' It has {len(buttons)} button(s); a press comes back to you as a [Pressed ...] message.'
            if buttons else '')
    return ToolOutcome.success(f'{what} (card_id {clean["id"]}).{hint}', card=clean)
