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
    assert state == {'mode': '', 'light': 'signal', 'dark': 'signal', 'themes': []}

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
        state = themes.project(await themes.get_settings(str(amy.id)))
        assert state['dark'] == 'forest' and state['light'] == 'tokyo-night' and state['mode'] == 'dark'

        again = _text(await _tool(action='create', name='Forest', dark_background='#0f1a14',
                                  dark_text='#e0f0e6', dark_accent='#ff0000'))
        assert again.startswith('Theme changed: Forest')
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
