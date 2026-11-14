"""Agent cards: the Show Card tool, its checks, and cards in chat apps."""
import asyncio
from types import SimpleNamespace

import pytest

from cognitrix.cards import CardError, button_message, buttons_of, normalize, to_markdown

WEEKLY = {
    'type': 'Card', 'props': {'title': 'Weekly sales', 'subtitle': 'Nov 7-13'},
    'children': [
        {'type': 'Fields', 'children': [
            {'type': 'Field', 'props': {'label': 'Revenue', 'value': '$48,210', 'change': '+12%', 'tone': 'good'}},
            {'type': 'Field', 'props': {'label': 'Refund rate', 'value': '1.8%', 'change': '+0.6 pt', 'tone': 'bad'}},
        ]},
        {'type': 'Chart', 'props': {'kind': 'bar', 'title': 'Revenue by day', 'unit': '$k',
                                    'labels': ['Mon', 'Tue', 'Wed'], 'series': [{'name': 'Revenue', 'values': [5.1, 6.2, 7.4]}]}},
        {'type': 'Table', 'props': {'title': 'Top products', 'columns': ['Product', 'Units', 'Revenue'],
                                    'rows': [['Linen shirt', 212, '$9,540'], ['Canvas tote', 340, '$6,120']]}},
        {'type': 'Actions', 'children': [
            {'type': 'Button', 'props': {'label': 'Email to team', 'action': 'email_team', 'style': 'primary'}},
            {'type': 'Button', 'props': {'label': 'Look into refunds', 'value': 'linen'}},
            {'type': 'LinkButton', 'props': {'label': 'Open report', 'url': 'https://example.com/r'}},
        ]},
    ],
}


def test_a_card_comes_out_in_one_clean_shape():
    card = normalize(WEEKLY)
    assert card['id'].startswith('card_') and card['title'] == 'Weekly sales'
    kinds = [b['type'] for b in card['blocks']]
    assert kinds == ['fields', 'chart', 'table', 'actions']
    assert card['blocks'][0]['items'][1] == {'label': 'Refund rate', 'value': '1.8%', 'tone': 'bad', 'change': '+0.6 pt'}
    table = card['blocks'][2]
    assert table['rows'][0] == ['Linen shirt', '212', '$9,540'] and table['numeric'] == [False, True, True]
    buttons = buttons_of(card)
    assert [b['action'] for b in buttons] == ['email_team', 'look_into_refunds']
    assert button_message(card, buttons[1]) == (
        '[Pressed "Look into refunds" on card "Weekly sales" (action: look_into_refunds, value: linen)]')


def test_json_render_flat_specs_and_loose_shapes_work():
    flat = {'root': 'c', 'elements': {
        'c': {'type': 'Card', 'props': {'title': 'Flat'}, 'children': ['t', 'p']},
        't': {'type': 'CardText', 'props': {'text': 'Hello **there**'}},
        'p': {'type': 'Chart', 'props': {'type': 'pie', 'data': [{'label': 'A', 'value': 3}, {'label': 'B', 'value': 1}]}},
    }}
    card = normalize(flat)
    assert card['blocks'][0] == {'type': 'text', 'text': 'Hello **there**'}
    assert card['blocks'][1]['kind'] == 'pie' and card['blocks'][1]['series'][0]['values'] == [3.0, 1.0]
    # Props straight on the element, and a lone element without a Card around it.
    loose = normalize({'type': 'Table', 'columns': ['a'], 'rows': [{'a': 1}]})
    assert loose['blocks'][0]['rows'] == [['1']]


def test_problems_come_back_as_readable_issues():
    with pytest.raises(CardError) as err:
        normalize({'type': 'Card', 'children': [
            {'type': 'Table', 'props': {'columns': ['a', 'b'], 'rows': [[1]]}},
            {'type': 'Chart', 'props': {'kind': 'radar', 'labels': ['x'], 'values': [1]}},
            {'type': 'Chart', 'props': {'kind': 'line', 'labels': ['x', 'y'], 'values': [1, 'lots']}},
            {'type': 'Gizmo'},
            {'type': 'LinkButton', 'props': {'label': 'Bad', 'url': 'javascript:alert(1)'}},
            {'type': 'Image', 'props': {'url': 'http://insecure.example/x.png'}},
        ]})
    issues = err.value.issues
    assert 'children[0].rows[0]: has 1 cells but there are 2 columns' in issues
    assert any('radar' in i for i in issues)
    assert any('must all be numbers' in i for i in issues)
    assert any('unknown element type "Gizmo"' in i for i in issues)
    assert any('http(s) url' in i for i in issues)
    assert any('an Image needs an https url' in i for i in issues)
    with pytest.raises(CardError):
        normalize({'type': 'Card'})
    with pytest.raises(CardError):
        normalize({'type': 'Card', 'children': [{'type': 'Text', 'text': 'x'}] * 200})


def test_markdown_version_for_chat_apps():
    card = normalize(WEEKLY)
    md = to_markdown(card)
    assert md.startswith('**Weekly sales**\n_Nov 7-13_')
    assert '**Revenue:** $48,210 (+12%)' in md
    assert 'Wed ████████████ 7.4 $k' in md
    assert 'Linen shirt    212   $9,540' in md
    assert '[Open report](https://example.com/r)' in md
    assert 'Email to team' not in md  # sent as a real button
    assert 'Reply with: "Email to team" · "Look into refunds"' in to_markdown(card, numbered_buttons=True)


async def test_show_card_tool_returns_the_card_or_the_issues():
    from cognitrix.tools.base import ToolManager

    tool = ToolManager.get_by_name('show_card')
    assert tool is not None and tool.name == 'Show Card'
    ok = (await tool.run(card=WEEKLY, parent=None)).outcome
    assert ok.status == 'success' and ok.card['title'] == 'Weekly sales'
    assert 'card_id card_' in ok.text and '2 button(s)' in ok.text
    assert '"blocks"' not in ok.model_content()  # the model gets text, not the whole card again
    as_text = (await tool.run(card='{"type": "Card", "props": {"title": "From text"}}', parent=None)).outcome
    assert as_text.card['title'] == 'From text'
    bad = (await tool.run(card={'type': 'Card', 'children': [{'type': 'Gizmo'}]}, parent=None)).outcome
    assert bad.status == 'error' and 'Fix these and call Show Card again' in bad.text and 'Gizmo' in bad.text
    assert bad.card is None


def test_show_card_never_asks_for_approval():
    from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel

    assert DestructiveOpDetector().analyze('show_card', {'card': WEEKLY}).risk_level is RiskLevel.LOW


# --- chat apps ----------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'cards.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield


async def _async(value):
    return value


class CardSession:
    id = 's1'
    chat: list = []

    def __init__(self):
        self.prompts: list[str] = []

    async def __call__(self, text, agent, *, output, **kwargs):
        self.prompts.append(text)
        await output({'type': 'generate', 'content': 'Here is the week.'})
        await output({'type': 'tool', 'status': 'completed', 'tool_name': 'Show Card',
                      'outcome': {'status': 'success', 'text': 'Showed', 'card': normalize(WEEKLY)}})
        await output({'type': 'generate', 'content': 'Refunds are up.'})

    async def save(self):
        pass


async def test_a_card_reaches_a_chat_app_with_buttons_and_a_press_goes_to_the_agent(db):
    from tests.test_chat_channels import _channel, _msg, _service

    channel = await _channel(chat_id='c1', peer_id='p1', link_code='')
    service, transport = await _service(channel)
    session = CardSession()
    service._agent = lambda _c: _async(SimpleNamespace(name='Analyst', id='a1'))
    service._session = lambda _c, _a: _async(session)

    await service._on_message(channel.id, _msg('how did sales go?'))
    await service._turns[channel.id]
    texts = [t for _, t, _ in transport.sent]
    assert texts[0] == 'Here is the week.'  # text before the card goes first
    card_msg = next(item for item in transport.sent if item[2])
    assert any('Weekly sales' in t for t in texts) and any('$48,210' in t for t in texts)
    assert [b.label for b in card_msg[2]] == ['Email to team', 'Look into refunds']
    assert card_msg[2][0].style == 'primary'
    assert texts[-1] == 'Refunds are up.'

    from cognitrix.channels.base import ButtonPress

    ref = str(transport.sent.index(card_msg) + 1)
    await service._on_button(channel.id, ButtonPress('c1', 'p1', card_msg[2][0].data, ref))
    await service._turns[channel.id]
    assert session.prompts[-1] == '[Pressed "Email to team" on card "Weekly sales" (action: email_team)]'
    assert transport.acks[-1] == 'You pressed Email to team.'

    await service._on_button(channel.id, ButtonPress('c1', 'p1', 'cd:card_gone:0', ref))
    assert transport.acks[-1].startswith('This card has expired')
    await asyncio.sleep(0)


def test_the_short_form_models_tend_to_write():
    card = normalize({
        'title': 'Weekly store summary',
        'metrics': [{'label': 'Revenue', 'value': '$48,210', 'delta': '+12%'},
                    {'label': 'Refund rate', 'value': '1.8%', 'delta': '+0.6 pts', 'trend': 'bad'}],
        'chart': {'type': 'bar', 'title': 'Daily revenue', 'data': [{'label': 'Mon', 'value': 5.1}, {'label': 'Tue', 'value': 6.2}]},
        'table': {'columns': ['Product', 'Units'], 'rows': [['Linen shirt', '212']]},
        'buttons': [{'label': 'Dig into the refunds', 'message': "What's driving refunds?"}],
    })
    assert [b['type'] for b in card['blocks']] == ['fields', 'chart', 'table', 'actions']
    assert card['blocks'][0]['items'][1]['tone'] == 'bad'
    assert card['blocks'][3]['buttons'][0] == {
        'label': 'Dig into the refunds', 'action': 'dig_into_the_refunds', 'style': 'default',
        'value': "What's driving refunds?"}
    with pytest.raises(CardError) as err:
        normalize({'title': 'x', 'widgets': [{'a': 1}]})
    assert 'unknown part "widgets"' in err.value.issues[0]


def test_series_written_as_points():
    card = normalize({'title': 'W', 'chart': {'type': 'bar', 'series': [
        {'name': 'Last', 'data': [{'label': 'Mon', 'value': 1}, {'label': 'Tue', 'value': 2}]},
        {'name': 'This', 'data': [{'label': 'Mon', 'value': 3}, {'label': 'Tue', 'value': 4}]}]}})
    chart = card['blocks'][0]
    assert chart['labels'] == ['Mon', 'Tue']
    assert [s['values'] for s in chart['series']] == [[1, 2], [3, 4]]
