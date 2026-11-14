"""Custom themes (Settings > Appearance) and the Set Theme agent tool.

- Theme files are validated the same way the frontend does.
- Built-in ids match frontend/src/lib/theme/presets.json.
- Each person's themes and picks are kept on the account.
- Set Theme lists, applies, switches mode and creates themes, and its result
  carries the previous picks so the chat can offer Undo.
"""
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

PRESETS = Path(__file__).resolve().parents[1] / 'frontend' / 'src' / 'lib' / 'theme' / 'presets.json'


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'themes.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield


async def _user(name, email):
    from cognitrix.models import User

    await User(name=name, email=email, password='x' * 20).save()
    return await User.find_one({'email': email})


@contextmanager
def _as(user=None, **kw):
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

    token = set_execution_context(ToolExecutionContext(user_id=str(user.id) if user else None, **kw))
    try:
        yield
    finally:
        reset_execution_context(token)


async def _tool(**params):
    from cognitrix.tools.base import ToolManager

    tool = ToolManager.get_by_name('set_theme')
    return await tool.run(**params)


def _text(result) -> str:
    return getattr(result, 'text', None) or str(result)


# --- format -------------------------------------------------------------------

def test_built_in_ids_match_the_frontend_presets():
    from cognitrix import themes

    presets = json.loads(PRESETS.read_text())
    assert {p['id']: p['name'] for p in presets} == {k: v['name'] for k, v in themes.BUILT_IN_THEMES.items()}
    for preset in presets:
        assert tuple(m for m in ('light', 'dark') if preset.get(m)) == themes.BUILT_IN_THEMES[preset['id']]['modes']
        themes.clean_theme(preset)  # every preset is a valid theme file


def test_clean_theme_tidies_and_checks():
    from cognitrix import themes

    out = themes.clean_theme({
        'name': '  Night Fall ', 'dark': {'background': '#1A1B26', 'text': 'c0caf5', 'accent': '#7af',
                                         'colors': {'danger': '#f7768e', 'line': 'rgba(255,255,255,.1)', 'nope': '#000'}},
        'contrast': 140, 'fonts': {'code': 'Fira Code', 'ui': ''},
    })
    assert out['id'] == 'night-fall'
    assert out['dark'] == {'background': '#1a1b26', 'text': '#c0caf5', 'accent': '#77aaff',
                           'colors': {'danger': '#f7768e', 'line': 'rgba(255,255,255,.1)'}}
    assert out['contrast'] == 100
    assert out['fonts'] == {'ui': None, 'code': 'Fira Code'}


@pytest.mark.parametrize('raw, reason', [
    ({'name': 'x'}, 'variant'),
    ({'name': 'x', 'dark': {'background': 'red', 'text': '#fff', 'accent': '#fff'}}, 'hex'),
    ({'name': 'x', 'dark': {'background': '#000', 'text': '#fff', 'accent': '#fff', 'colors': {'fg': 'url(x)'}}}, 'colour'),
    ({'name': 'x', 'light': {'background': '#000', 'text': '#fff', 'accent': '#fff'}, 'fonts': {'ui': "x'; } body{"}}, 'font'),
    ({'dark': {'background': '#000', 'text': '#fff', 'accent': '#fff'}}, 'name'),
    ({'format': 'other', 'name': 'x'}, 'Cognitrix'),
])
def test_bad_themes_are_refused(raw, reason):
    from cognitrix import themes

    with pytest.raises(themes.ThemeError) as err:
        themes.clean_theme(raw)
    assert reason in str(err.value)


# --- account state --------------------------------------------------------------

async def test_themes_are_per_person_and_picks_fall_back(db):
    from cognitrix import themes

    amy = await _user('Amy', 'amy@example.com')
    bob = await _user('Bob', 'bob@example.com')
    state = themes.project(await themes.get_settings(str(amy.id)))
    assert state == {'mode': '', 'light': 'signal', 'dark': 'signal', 'themes': [], 'artwork': {}, 'drawings': []}

    dusk = {'name': 'Dusk', 'dark': {'background': '#101018', 'text': '#eeeeee', 'accent': '#ff8800'}}
    theme, state = await themes.save_theme(str(amy.id), dusk)
    assert theme['id'] == 'dusk' and [t['id'] for t in state['themes']] == ['dusk']
    state = await themes.set_selection(str(amy.id), dark='dusk', mode='dark')
    assert (state['dark'], state['mode']) == ('dusk', 'dark')

    with pytest.raises(themes.ThemeError):
        await themes.set_selection(str(amy.id), light='dusk')  # no light version
    with pytest.raises(themes.ThemeError):
        await themes.save_theme(str(amy.id), {**dusk, 'id': 'nord'}, theme_id='nord')  # built-in id

    assert themes.project(await themes.get_settings(str(bob.id)))['themes'] == []

    state = await themes.delete_theme(str(amy.id), 'dusk')
    assert state['dark'] == 'signal' and state['themes'] == []


async def test_theme_api_round_trip(db):
    from fastapi import HTTPException

    from cognitrix.api.routes import themes as routes
    from cognitrix.common.security import AuthContext

    amy = await _user('Amy', 'amy@example.com')
    ctx = AuthContext(user=amy)
    out = await routes.put_custom_theme('sea', {'name': 'Sea', 'light': {'background': '#f0f8ff', 'text': '#102030', 'accent': '#0077cc'}}, ctx)
    assert out['theme']['id'] == 'sea'
    out = await routes.put_theme(routes.Selection(light='sea', mode='system'), ctx)
    assert out['light'] == 'sea' and out['mode'] == 'system'
    with pytest.raises(HTTPException) as err:
        await routes.put_theme(routes.Selection(mode='purple'), ctx)
    assert err.value.status_code == 400
    assert (await routes.get_theme(ctx))['light'] == 'sea'


# --- Set Theme tool --------------------------------------------------------------

async def test_set_theme_tool_applies_lists_and_creates(db):
    from cognitrix import themes

    amy = await _user('Amy', 'amy@example.com')
    with _as(amy):
        listed = _text(await _tool(action='list'))
        assert 'Tokyo Night' in listed and 'light: Signal' in listed

        done = _text(await _tool(action='apply', theme='tokyo night', mode='dark'))
        assert done.startswith('Theme changed: Tokyo Night')
        previous = json.loads(done.split('Previous: ', 1)[1])
        assert previous == {'mode': '', 'light': 'signal', 'dark': 'signal'}
        state = themes.project(await themes.get_settings(str(amy.id)))
        assert (state['light'], state['dark'], state['mode']) == ('tokyo-night', 'tokyo-night', 'dark')

        made = _text(await _tool(action='create', name='Forest', dark_background='#0f1a14',
                                 dark_text='#e0f0e6', dark_accent='#5ad17a'))
        assert made.startswith('Theme changed: Forest')
        assert json.loads(made.split('Previous: ', 1)[1])['created'] == 'forest'
        state = themes.project(await themes.get_settings(str(amy.id)))
        assert state['dark'] == 'forest' and state['light'] == 'tokyo-night' and state['mode'] == 'dark'

        again = _text(await _tool(action='create', name='Forest', dark_background='#0f1a14',
                                  dark_text='#e0f0e6', dark_accent='#ff0000'))
        assert again.startswith('Theme changed: Forest')
        undo = json.loads(again.split('Previous: ', 1)[1])
        assert undo['theme']['dark']['accent'] == '#5ad17a' and 'created' not in undo
        state = themes.project(await themes.get_settings(str(amy.id)))
        assert len(state['themes']) == 1 and state['themes'][0]['dark']['accent'] == '#ff0000'

        assert 'mode: light' in _text(await _tool(action='mode', mode='light'))
        assert 'no theme' in _text(await _tool(action='apply', theme='Barbie')).lower()
        assert 'all three' in _text(await _tool(action='create', name='Half', light_background='#ffffff'))


async def test_set_theme_needs_a_person(db):
    with _as():
        assert 'signed-in' in _text(await _tool(action='list'))


def test_set_theme_does_not_ask_first():
    from cognitrix.safety.destructive_ops import _KNOWN_LOW

    assert 'set_theme' in _KNOWN_LOW


# --- artwork ----------------------------------------------------------------------

ARTWORK_TS = Path(__file__).resolve().parents[1] / 'frontend' / 'src' / 'lib' / 'theme' / 'artwork.ts'


def test_artwork_names_match_the_frontend():
    import re

    from cognitrix import themes

    block = re.search(r'ARTWORK_NAMES: Record<string, string> = \{([^}]*)\}', ARTWORK_TS.read_text()).group(1)
    names = dict(re.findall(r"'?([\w-]+)'?: '([^']+)'", block))
    assert names == themes.ARTWORK_NAMES
    presets = {p['id']: p['artwork']['id'] for p in json.loads(PRESETS.read_text())}
    assert presets == {k: v['artwork'] for k, v in themes.BUILT_IN_THEMES.items()}


def test_artwork_svg_is_cleaned_to_plain_shapes():
    from cognitrix import themes

    dirty = (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 400 160" onload="x()">'
        '<script>alert(1)</script><style>*{}</style><foreignObject><div/></foreignObject>'
        '<image href="https://evil.example/a.png"/><use xlink:href="#a"/><text>hi</text>'
        '<a href="javascript:x"><rect width="9" height="9"/></a>'
        '<circle cx="10" cy="10" r="5" fill="text" onclick="x()" style="stroke:accent; opacity:.5"/>'
        '<rect x="1" y="1" width="2" height="2" fill="url(https://evil)" stroke="red" transform="rotate(4) url(x)"/>'
        '<path d="M0 0 L10 10 Z" fill="#ABC" mask="url(#m)" filter="url(http://x)"/>'
        '</svg>')
    clean = themes.clean_artwork_svg(dirty)
    assert clean == ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160">'
                     '<circle cx="10" cy="10" r="5" fill="fg" stroke="accent" opacity=".5"/>'
                     '<rect x="1" y="1" width="2" height="2"/>'
                     '<path d="M0 0 L10 10 Z" fill="#abc" mask="url(#m)"/></svg>')
    # Cleaning is stable, so the frontend can clean what the server sends again.
    assert themes.clean_artwork_svg(clean) == clean


@pytest.mark.parametrize('svg, reason', [
    ('', 'empty'),
    ('<svg viewBox="0 0 4 4"><circle r="1"/>' + ' ' * 30000 + '</svg>', 'too large'),
    ('<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg viewBox="0 0 4 4">&a;</svg>', 'DOCTYPE'),
    ('<html><body/></html>', '<svg>'),
    ('<svg><circle r="1"/></svg>', 'viewBox'),
    ('<svg viewBox="0 0 4 4"><script>x</script></svg>', 'Nothing'),
    ('not xml', 'not valid'),
])
def test_bad_artwork_is_refused(svg, reason):
    from cognitrix import themes

    with pytest.raises(themes.ThemeError, match=reason):
        themes.clean_artwork_svg(svg)


def test_theme_files_carry_artwork():
    from cognitrix import themes

    base = {'name': 'Dusk', 'dark': {'background': '#101018', 'text': '#eeeeee', 'accent': '#ff8800'}}
    assert themes.clean_theme({**base, 'artwork': {'id': 'aurora', 'strength': 140}})['artwork'] == {'id': 'aurora', 'strength': 100}
    drawn = themes.clean_theme({**base, 'artwork': {'svg': '<svg viewBox="0 0 4 4"><circle r="1" fill="accent"/></svg>'}})
    assert drawn['artwork']['name'] == 'Your drawing' and 'fill="accent"' in drawn['artwork']['svg']
    with pytest.raises(themes.ThemeError, match='Unknown artwork'):
        themes.clean_theme({**base, 'artwork': {'id': 'mona-lisa'}})


async def test_artwork_choices_are_per_person(db):
    from cognitrix import themes

    amy = await _user('Amy', 'amy@example.com')
    bob = await _user('Bob', 'bob@example.com')
    state = await themes.set_artwork(str(amy.id), 'nord', {'id': 'skyline'})
    assert state['artwork'] == {'nord': {'id': 'skyline'}}
    assert themes.project(await themes.get_settings(str(bob.id)))['artwork'] == {}
    row = await themes.get_settings(str(amy.id))
    assert themes.theme_artwork('nord', row) == {'id': 'skyline'}
    assert themes.theme_artwork('solarized', row) == {'id': 'dunes'}
    assert (await themes.set_artwork(str(amy.id), 'nord', None))['artwork'] == {}
    with pytest.raises(themes.ThemeError, match='no theme'):
        await themes.set_artwork(str(amy.id), 'barbie', {'id': 'none'})

    # Deleting a theme drops its artwork choice too.
    await themes.save_theme(str(amy.id), {'name': 'Dusk', 'dark': {'background': '#101018', 'text': '#eeeeee', 'accent': '#ff8800'}})
    await themes.set_artwork(str(amy.id), 'dusk', {'id': 'blueprint'})
    assert 'dusk' not in (await themes.delete_theme(str(amy.id), 'dusk'))['artwork']


async def test_theme_artwork_api(db):
    from httpx import ASGITransport, AsyncClient

    from cognitrix.api.main import app
    from cognitrix.common.security import AuthContext, jwt_only

    amy = await _user('Amy', 'amy@example.com')
    app.dependency_overrides[jwt_only] = lambda: AuthContext(user=amy)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            res = await client.put('/api/v1/theme/artwork/signal', json={'svg': '<svg viewBox="0 0 4 4"><rect width="4" height="2" fill="warn"/><script/></svg>', 'name': 'Bar'})
            assert res.status_code == 200, res.text
            assert res.json()['artwork']['signal']['name'] == 'Bar'
            assert '<script' not in res.json()['artwork']['signal']['svg']
            assert (await client.put('/api/v1/theme/artwork/signal', json={'id': 'nope'})).status_code == 400
            assert (await client.delete('/api/v1/theme/artwork/signal')).json()['artwork'] == {}
    finally:
        app.dependency_overrides.pop(jwt_only, None)


async def test_set_theme_tool_draws_and_picks_artwork(db):
    from cognitrix import themes

    amy = await _user('Amy', 'amy@example.com')
    lighthouse = ('<svg viewBox="0 0 400 160"><path d="M216 128L222 70H242L248 128Z" fill="text"/>'
                  '<rect x="224" y="52" width="16" height="12" fill="warn"/></svg>')
    with _as(amy):
        await _tool(action='apply', theme='nord', mode='dark')
        assert 'Artwork: Nord: Aurora' in _text(await _tool(action='list'))

        drawn = _text(await _tool(action='artwork', svg=lighthouse, name='Lighthouse'))
        assert drawn.startswith('Theme changed: Lighthouse artwork on Nord')
        assert json.loads(drawn.split('Previous: ', 1)[1])['artwork'] == {'nord': None}
        state = themes.project(await themes.get_settings(str(amy.id)))
        assert state['artwork']['nord']['name'] == 'Lighthouse' and state['dark'] == 'nord'

        picked = _text(await _tool(action='artwork', artwork='Moon cat', theme='solarized'))
        assert picked.startswith('Theme changed: Moon cat artwork on Solarized')
        hidden = _text(await _tool(action='artwork', artwork='none'))
        assert json.loads(hidden.split('Previous: ', 1)[1])['artwork']['nord']['name'] == 'Lighthouse'
        assert 'no artwork' in _text(await _tool(action='artwork', artwork='Mona Lisa')).lower()
        assert 'not valid' in _text(await _tool(action='artwork', svg='<svg nope')).lower()


async def test_drawings_survive_picking_other_artwork(db):
    """A drawing is never lost: picking other artwork, replacing or deleting its theme keeps it saved."""
    from cognitrix import themes

    amy = await _user('Amy', 'amy@example.com')
    uid = str(amy.id)
    pumpkin = '<svg viewBox="0 0 400 160"><circle cx="200" cy="120" r="30" fill="warn"/></svg>'
    bat = '<svg viewBox="0 0 400 160"><path d="M10 10L20 20Z" fill="violet"/></svg>'

    await themes.set_artwork(uid, 'nord', {'svg': pumpkin, 'name': 'Pumpkin'})
    state = await themes.set_artwork(uid, 'nord', {'id': 'blueprint'})
    assert state['artwork']['nord'] == {'id': 'blueprint'}
    assert [d['name'] for d in state['drawings']] == ['Pumpkin']
    # Picking it again from the library brings it back.
    key = state['drawings'][0]['key']
    state = await themes.set_artwork(uid, 'nord', {'svg': state['drawings'][0]['svg'], 'name': 'Pumpkin'})
    assert state['artwork']['nord']['name'] == 'Pumpkin' and len(state['drawings']) == 1

    # A theme's own drawing is kept too, even after the theme is deleted.
    dusk = {'name': 'Dusk', 'dark': {'background': '#101018', 'text': '#eeeeee', 'accent': '#ff8800'},
            'artwork': {'svg': bat, 'name': 'Bat'}}
    _, state = await themes.save_theme(uid, dusk)
    assert [d['name'] for d in state['drawings']] == ['Bat', 'Pumpkin']
    state = await themes.delete_theme(uid, 'dusk')
    assert [d['name'] for d in state['drawings']] == ['Bat', 'Pumpkin']
    assert themes.find_drawing('bat', await themes.get_settings(uid))['name'] == 'Bat'

    # Removing from the library; one still in use stays listed.
    bat_key = state['drawings'][0]['key']
    assert [d['name'] for d in (await themes.delete_drawing(uid, bat_key))['drawings']] == ['Pumpkin']
    assert [d['name'] for d in (await themes.delete_drawing(uid, key))['drawings']] == ['Pumpkin']


async def test_drawings_in_use_before_the_library_are_kept(db):
    """Rows saved before drawings were kept: the drawing in use joins the library on the next change."""
    from cognitrix import themes

    amy = await _user('Amy', 'amy@example.com')
    uid = str(amy.id)
    art = themes.clean_artwork({'svg': '<svg viewBox="0 0 4 4"><circle r="1" fill="accent"/></svg>', 'name': 'Old'})
    await themes._save(uid, {'artwork': json.dumps({'nord': art})})
    state = await themes.set_artwork(uid, 'nord', {'id': 'skyline'})
    assert [d['name'] for d in state['drawings']] == ['Old']


async def test_set_theme_create_keeps_its_drawing_and_reuses_saved_ones(db):
    from cognitrix import themes

    amy = await _user('Amy', 'amy@example.com')
    pumpkin = '<svg viewBox="0 0 400 160"><circle cx="200" cy="120" r="30" fill="warn"/></svg>'
    with _as(amy):
        made = _text(await _tool(action='create', name='Haunted Harvest', dark_background='#15111c',
                                 dark_text='#fff1dd', dark_accent='#ff9b40', svg=pumpkin))
        assert made.startswith('Theme changed: Haunted Harvest')
        state = themes.project(await themes.get_settings(str(amy.id)))
        own = next(t for t in state['themes'] if t['id'] == 'haunted-harvest')
        assert own['artwork']['name'] == 'Haunted Harvest' and 'fill="warn"' in own['artwork']['svg']
        assert [d['name'] for d in state['drawings']] == ['Haunted Harvest']

        await _tool(action='artwork', artwork='blueprint', theme='haunted-harvest')
        assert 'Saved drawings (name, key): Haunted Harvest (' in _text(await _tool(action='list'))
        key = themes.project(await themes.get_settings(str(amy.id)))['drawings'][0]['key']
        assert f'Haunted Harvest ({key})' in _text(await _tool(action='list'))
        back = _text(await _tool(action='artwork', artwork='Haunted Harvest', theme='haunted-harvest'))
        assert back.startswith('Theme changed: Haunted Harvest artwork on Haunted Harvest')


def test_artwork_transforms_do_not_backtrack():
    import time

    from cognitrix import themes

    assert themes.clean_artwork_attribute('transform', 'translate(10 20) rotate(45), scale(2)') == 'translate(10 20) rotate(45), scale(2)'
    start = time.monotonic()
    assert themes.clean_artwork_attribute('transform', 'scale()' + ' scale()' * 400 + '!') is None
    assert time.monotonic() - start < 0.5
