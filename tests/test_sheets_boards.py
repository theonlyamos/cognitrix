"""Sheets and whiteboards: artifacts people and agents both edit.

- Agent input in several shapes becomes one canonical JSON.
- Formulas compute (ranges, other tabs, errors, cycles).
- People's edits save as versions; quick saves merge; a stale editor is refused.
- Agents read computed values and people's changes.
- The read-only frame renders a table / SVG with text escaped.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cognitrix.artifact_pages import documents as d


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings

    db_file = str(tmp_path / 'sheets.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setattr(settings, 'workdir', tmp_path)
    yield tmp_path


async def _user(name, email):
    from cognitrix.models import User

    await User(name=name, email=email, password='x' * 20).save()
    return await User.find_one({'email': email})


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


def _as(user, **kw):
    from cognitrix.tools.utils import ToolExecutionContext, set_execution_context

    return set_execution_context(ToolExecutionContext(user_id=str(user.id), **kw))


AGENT = SimpleNamespace(id='a1', name='Analyst')


async def _tool(name, **params):
    from cognitrix.tools.base import ToolManager

    return (await ToolManager.get_by_name(name).run(**params, parent=AGENT)).outcome


def _values(source, tab=None):
    data = json.loads(source)
    sheet = d.Sheet(data)
    name = tab or data['tabs'][0]['name']
    return {ref: sheet.shown(name, ref) for ref in data['tabs'][[t['name'] for t in data['tabs']].index(name)]['cells']}


def test_sheet_inputs_become_one_shape():
    rows = d.normalize_sheet(json.dumps([['Item', 'Cost'], ['Rent', 1200]]))
    assert json.loads(rows) == {'tabs': [{'name': 'Sheet1', 'cells': {'A1': 'Item', 'B1': 'Cost', 'A2': 'Rent', 'B2': 1200}}]}
    csv_text = json.loads(d.normalize_sheet('a,b\n1,2.5\n'))
    assert csv_text['tabs'][0]['cells'] == {'A1': 'a', 'B1': 'b', 'A2': 1, 'B2': 2.5}
    single = json.loads(d.normalize_sheet('{"name": "T", "cells": {"a1": "x"}, "styles": {"A1": {"bold": 1, "fmt": "evil"}}}'))
    assert single['tabs'][0]['cells'] == {'A1': 'x'} and single['tabs'][0]['styles'] == {'A1': {'bold': True}}
    dupes = json.loads(d.normalize_sheet('{"tabs": [{"name": "A"}, {"name": "a"}]}'))
    assert [t['name'] for t in dupes['tabs']] == ['A', 'a 2']
    shaped = json.loads(d.normalize_sheet(json.dumps([[{'value': 'Item', 'bold': True}, {'v': 5, 'format': 'money'}]])))
    assert shaped['tabs'][0]['cells'] == {'A1': 'Item', 'B1': 5}
    assert shaped['tabs'][0]['styles'] == {'A1': {'bold': True}, 'B1': {'fmt': 'currency'}}
    assert 'B1: 5 (shown "5.00")' in d.sheet_summary(json.dumps(shaped))
    for bad in ('{"tabs": []}', '{"cells": {"ZZZZ1": 1}}', '"text"', json.dumps({'tabs': [{}] * 21})):
        with pytest.raises(d.DocumentError):
            d.normalize_sheet(bad)


def test_formulas():
    src = d.normalize_sheet(json.dumps({'tabs': [
        {'name': 'Budget', 'rows': [
            ['Rent', 1200], ['Food', 300.5], ['Total', '=SUM(B1:B2)'], ['Avg', '=AVERAGE(B1:B2)'],
            ['Calc', '=(B3-1)*2+10%'], ['Join', '="Total: "&B3'], ['If', '=IF(B3>1000,"big","small")'],
            ['Pow', '=2^3^2'], ['Other', "='Rates 2026'!A1*2"], ['Div', '=1/0'], ['Name', '=NOPE(1)'],
            ['Cycle', '=B12'], ['Chain', '=B10+1'], ['Round', '=ROUND(2/3,2)'], ['Bad', '=1+'],
            ['Bool', '=AND(TRUE, B1>1)'], ['Count', '=COUNT(B1:B4)'], ['Text', '=UPPER(A1)&LEN(A1)'],
        ]},
        {'name': 'Rates 2026', 'cells': {'A1': 21}},
    ]}))
    v = _values(src, 'Budget')
    assert v['B3'] == 1500.5 and v['B4'] == 750.25 and v['B5'] == 2999.1
    assert v['B6'] == 'Total: 1500.5' and v['B7'] == 'big' and v['B8'] == 512 and v['B9'] == 42
    assert v['B10'] == '#DIV/0!' and v['B11'] == '#NAME?' and v['B12'] == '#CYCLE!' and v['B13'] == '#DIV/0!'
    assert v['B14'] == 0.67 and v['B15'] == '#ERROR!' and v['B16'] is True and v['B17'] == 4 and v['B18'] == 'RENT4'
    odd = _values(d.normalize_sheet(json.dumps([['=IF(1)', '=(-8)^0.5', '=10^400']])))
    assert odd == {'A1': True, 'B1': '#VALUE!', 'C1': '#VALUE!'}
    summary = d.sheet_summary(src)
    assert 'B3: =SUM(B1:B2) -> 1500.5' in summary and 'Tab "Rates 2026"' in summary


def test_board_inputs_are_cleaned():
    board = json.loads(d.normalize_board(json.dumps({'items': [
        {'id': 'n1', 'type': 'note', 'text': 'Goals'},
        {'id': 'n2', 'type': 'rect', 'text': 'Plan', 'x': 'nope', 'y': 1e12, 'color': 'red; x'},
        {'type': 'arrow', 'from': 'n1', 'to': 'n2'},
        {'type': 'arrow', 'from': 'n1', 'to': 'gone'},
        {'type': 'draw', 'points': [[0, 0], [5, 5]], 'color': '#ff0000'},
        {'type': 'mystery', 'text': 'becomes a note'},
    ]})))
    kinds = [i['type'] for i in board['items']]
    assert kinds == ['note', 'rect', 'arrow', 'draw', 'note']
    assert board['items'][0]['x'] == 40 and board['items'][1]['y'] == d.COORD_LIMIT
    assert board['items'][1]['color'] == 'blue' and board['items'][3]['color'] == '#ff0000'
    assert 'arrow from "Goals" to "Plan"' in d.board_summary(json.dumps(board))
    # Nodes and edges, as many diagram tools write them.
    graph = json.loads(d.normalize_board(json.dumps({'nodes': [{'id': 'a', 'label': 'Design', 'shape': 'sticky'},
                                                               {'id': 'b', 'label': 'Ship', 'shape': 'circle'}],
                                                     'edges': [{'source': 'a', 'target': 'b'}]})))
    assert [(i['type'], i.get('text')) for i in graph['items']] == [('note', 'Design'), ('ellipse', 'Ship'), ('arrow', None)]
    with pytest.raises(d.DocumentError):
        d.normalize_board('{"diagram": "x"}')


def test_read_only_views_escape_text():
    board = d.normalize_board(json.dumps({'items': [{'type': 'note', 'text': '<script>alert(1)</script>'}]}))
    sheet = d.normalize_sheet(json.dumps([['<img src=x onerror=alert(1)>']]))
    assert '<script>' not in d.render_board(board) and '&lt;script&gt;' in d.render_board(board)
    assert '<img' not in d.render_sheet(sheet)


async def test_agent_publishes_reads_and_people_edit(db):
    from cognitrix.api.routes import artifact_pages as routes

    amos = await _user('Amos', 'amos@example.com')
    _as(amos, session_id='s1')
    made = await _tool('publish_artifact', title='Budget', format='sheet',
                       content=json.dumps({'tabs': [{'name': 'Budget', 'rows': [['Rent', 1200], ['Total', '=SUM(B1:B1)']]}]}))
    assert made.status == 'success', made.text
    pid = made.entities[0].id

    # The person edits in the app.
    me = _ctx(amos)
    source = (await routes.page_source(pid, None, me))['source']
    data = json.loads(source)
    data['tabs'][0]['cells']['A3'], data['tabs'][0]['cells']['B3'] = 'Food', 300
    data['tabs'][0]['cells']['B2'] = '=B1+B3'
    saved = await routes.save_content(pid, routes.ContentBody(content=json.dumps(data), base_version=1), me)
    assert saved['version'] == 2
    # A second save soon after goes into the same version.
    data['tabs'][0]['cells']['A4'] = 'Note'
    again = await routes.save_content(pid, routes.ContentBody(content=json.dumps(data), base_version=2), me)
    assert again['version'] == 2

    # The agent sees the person's change, with computed values.
    read = await _tool('read_artifact', artifact_id=pid)
    assert 'B2: =B1+B3 -> 1500' in read.text and 'A4: Note' in read.text

    # The agent updates; an editor still on the old version is refused.
    await _tool('publish_artifact', title='Budget', format='sheet', artifact_id=pid, content=source)
    with pytest.raises(HTTPException) as stale:
        await routes.save_content(pid, routes.ContentBody(content=json.dumps(data), base_version=2), me)
    assert stale.value.status_code == 409

    # Bad content and format switches are refused.
    with pytest.raises(HTTPException):
        await routes.save_content(pid, routes.ContentBody(content='{"tabs": []}', base_version=3), me)
    switched = await _tool('publish_artifact', title='Budget', format='html', artifact_id=pid, content='<p>hi</p>')
    assert switched.status == 'error' and 'is a sheet' in switched.text


async def test_viewers_cannot_edit_and_people_can_start_blank_ones(db):
    from cognitrix.api.routes import artifact_pages as routes

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    made = await routes.new_document(routes.NewDocument(format='whiteboard'), _ctx(amos))
    page = await routes.get_page(made['id'], _ctx(amos))
    assert page['format'] == 'whiteboard' and page['title'] == 'Untitled whiteboard'
    await routes.set_sharing(made['id'], routes.SharingUpdate(people=[routes.ShareEntry(user_id=str(kofi.id), role='view')]),
                             _ctx(amos))
    with pytest.raises(HTTPException) as denied:
        await routes.save_content(made['id'], routes.ContentBody(content='{"items": []}', base_version=1), _ctx(kofi))
    assert denied.value.status_code == 403
    from cognitrix.artifact_pages import store

    frame_html = routes._frame_response(await store.get_page(made['id']), 1)
    assert '<svg' in frame_html.body.decode()
