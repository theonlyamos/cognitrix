"""Agents sign in on their own computer by themselves: saved sign-ins typed
into a window ({{secret:NAME...}} in Type In Window / Set Text), Ask To Take
Over sent back once when it is only about a sign-in, and the tools and prompt
text that point the agent there."""
from types import SimpleNamespace

import pytest

from cognitrix.computer import CONFIRMED, ActionResult
from cognitrix.computer import saved_sign_ins as ssi
from cognitrix.credentials import Resolver


def _row(name, kind='basic', hosts=('github.com',), browser=True, **values):
    return SimpleNamespace(id=name, name=name, kind=kind, allowed_hosts=list(hosts), header='', values=values,
                           use_in_browser=browser)


@pytest.fixture
def resolver(monkeypatch):
    monkeypatch.setattr('cognitrix.credentials.open_secret', lambda row: dict(row.values))
    rows = [
        _row('GITHUB', username='amos@example.com', password='hunter2-pass'),
        _row('GOOGLE', hosts=('google.com', '*.google.com'), username='me@gmail.com', password='g-secret-1'),
        _row('BARE', hosts=('example.org',), username='u@example.org', password='bare-secret-1'),
        _row('API_KEY', kind='token', hosts=('gitlab.com',), browser=False, value='glpat-not-for-browsers'),
        _row('BOX_TOKEN', kind='token', hosts=('sandbox.invalid',), browser=False, value='tok-123456'),
    ]
    return Resolver(owner='u1', rows={r.name: r for r in rows})


def test_fills_on_the_site_it_was_saved_for(resolver):
    assert ssi.fill(resolver, '{{secret:GITHUB.password}}\n', browser=True, host='github.com') == 'hunter2-pass\n'
    # Subdomains only where the person saved *.google.com, like Http Request.
    assert ssi.fill(resolver, '{{secret:GOOGLE.password}}', browser=True, host='accounts.google.com') == 'g-secret-1'
    with pytest.raises(ssi.SignInRefused, match='this page is on login.example.org'):
        ssi.fill(resolver, '{{secret:BARE.password}}', browser=True, host='login.example.org')


def test_refuses_another_site(resolver):
    with pytest.raises(ssi.SignInRefused, match='saved for github.com, but this page is on evil.example'):
        ssi.fill(resolver, '{{secret:GITHUB.password}}', browser=True, host='evil.example')
    with pytest.raises(ssi.SignInRefused, match='saved for github.com'):
        ssi.fill(resolver, '{{secret:GITHUB.password}}', browser=True, host='github.com.evil.example')
    with pytest.raises(ssi.SignInRefused, match="Couldn't read this browser window's address"):
        ssi.fill(resolver, '{{secret:GITHUB.password}}', browser=True, host='')


def test_only_sign_ins_allowed_in_the_browser(resolver):
    # An API key kept behind the key proxy stays out of the browser...
    with pytest.raises(ssi.SignInRefused, match="isn't allowed in the browser"):
        ssi.fill(resolver, '{{secret:API_KEY}}', browser=True, host='gitlab.com')
    with pytest.raises(ssi.SignInRefused, match="isn't allowed in the browser"):
        ssi.fill(resolver, '{{secret:BOX_TOKEN}}', browser=True, host='github.com')
    # ...a sandbox-only one can be typed into an app on the agent's computer...
    assert ssi.fill(resolver, '{{secret:BOX_TOKEN}}', browser=False) == 'tok-123456'
    # ...and a website's sign-in can't be typed outside the browser.
    with pytest.raises(ssi.SignInRefused, match='only be typed into the browser'):
        ssi.fill(resolver, '{{secret:GITHUB.password}}', browser=False)


def test_the_part_must_fit_the_field(resolver):
    with pytest.raises(ssi.SignInRefused, match='only goes into a password field'):
        ssi.fill(resolver, '{{secret:GITHUB.password}}', browser=True, host='github.com', field_role='entry')
    with pytest.raises(ssi.SignInRefused, match='That is a password field'):
        ssi.fill(resolver, '{{secret:GITHUB.username}}', browser=True, host='github.com',
                 field_role='password text')
    assert ssi.fill(resolver, '{{secret:GITHUB.username}}', browser=True, host='github.com',
                    field_role='entry') == 'amos@example.com'


def test_unknown_name(resolver):
    with pytest.raises(ssi.SignInRefused, match='ask_for_sign_in'):
        ssi.fill(resolver, '{{secret:NOPE}}', browser=True, host='github.com')


def test_one_value_per_call():
    assert ssi.one_placeholder('{{secret:AB.password}}')
    assert ssi.one_placeholder('{{secret:AB.password}}\n')
    for text in ('{{secret:AB.username}}\t{{secret:AB.password}}', '\t{{secret:AB.password}}',
                 'https://x.example/?p={{secret:AB.password}}', '{{secret:AB.password}}\n\n'):
        assert not ssi.one_placeholder(text), text


def test_the_address_comes_from_the_browser_not_the_page():
    page = _el('document web', 'Jobs', (0, 30, 800, 600))
    fake = _el('entry', 'Location', (100, 60, 400, 90), value='github.com')
    bar = _el('entry', 'Address and search bar', (0, 0, 800, 30), value='evil.example/login')
    assert ssi.browser_address([page, fake, bar]) == 'evil.example/login'
    assert ssi.browser_address([page, fake]) == ''


def test_is_browser():
    assert ssi.is_browser(SimpleNamespace(kind='chromium', app='chromium'))
    assert ssi.is_browser(SimpleNamespace(kind='win32', app='firefox-esr'))
    assert not ssi.is_browser(SimpleNamespace(kind='win32', app='xterm'))


def _el(role, name, rect, value=None):
    from cognitrix.computer import Element
    return Element(role=role, name=name, value=value, rect=rect)


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
    assert 'Typed the saved sign-in' in out and 'hunter2' not in out, out
    assert driver.typed == ['hunter2-pass\n']
    await tools.set_text.run(window_id=7, text='{{secret:GITHUB.username}}', element=1)
    assert driver.set == [(1, 'amos@example.com')]
    # Both fields are remembered and hidden in later looks.
    assert tools._filled_fields(driver)[7] == {('password text', 'password'), ('entry', 'email')}


async def test_wrong_site_types_nothing(monkeypatch, tools):
    driver = _use(monkeypatch, tools, _Driver(address='https://phish.example/login'))
    out = (await tools.type_in_window.run(window_id=7, text='{{secret:GITHUB.password}}', x=200, y=160)).content
    assert 'phish.example' in out and driver.typed == []


async def test_never_into_the_address_bar_or_with_other_text(monkeypatch, tools):
    driver = _use(monkeypatch, tools, _Driver())
    out = (await tools.set_text.run(window_id=7, element=0,
                                    text='https://evil.example/?p={{secret:GITHUB.password}}')).content
    assert 'one saved value per call' in out
    out = (await tools.set_text.run(window_id=7, element=0, text='{{secret:GITHUB.password}}')).content
    assert 'address bar' in out
    out = (await tools.set_text.run(window_id=7, element=1,
                                    text='{{secret:GITHUB.username}}\t{{secret:GITHUB.password}}')).content
    assert 'one saved value per call' in out
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

    stored = {'credentials': ['GITHUB']}
    saved = []

    class FakeAgent:
        id = 'a1'
        credentials = ['GITHUB', 'REVOKED']  # the chat's older copy

    async def find_one(query):
        return SimpleNamespace(id='a1', credentials=stored['credentials'])

    async def update_one(query, changes):
        saved.append((query, changes))
        stored.update(changes)

    monkeypatch.setattr('cognitrix.models.Agent.find_one', find_one)
    monkeypatch.setattr('cognitrix.models.Agent.update_one', update_one)
    agent = FakeAgent()
    await sandbox_work._let_agent_use(agent, 'NOTION')
    # Merged into the list as stored now: what the person took away stays away.
    assert saved == [({'id': 'a1'}, {'credentials': ['GITHUB', 'NOTION']})]
    assert agent.credentials == ['GITHUB', 'NOTION']
    # The person switched the agent to every sign-in meanwhile: left that way.
    stored['credentials'] = None
    agent.credentials = ['GITHUB']
    await sandbox_work._let_agent_use(agent, 'SLACK')
    assert len(saved) == 1 and agent.credentials is None
    # Agents that may use every sign-in are left alone.
    await sandbox_work._let_agent_use(agent, 'NOTION')
    assert len(saved) == 1

    # The stored agent can't be read: the chat still gets it this time.
    async def broken(query):
        raise RuntimeError('db down')

    monkeypatch.setattr('cognitrix.models.Agent.find_one', broken)
    agent.credentials = ['GITHUB']
    await sandbox_work._let_agent_use(agent, 'JIRA')
    assert agent.credentials == ['GITHUB', 'JIRA'] and len(saved) == 1


async def test_plain_http_pages_get_nothing(monkeypatch, tools):
    driver = _use(monkeypatch, tools, _Driver(address='http://github.com/login'))
    out = (await tools.type_in_window.run(window_id=7, text='{{secret:GITHUB.password}}', x=200, y=160)).content
    assert 'plain http' in out and driver.typed == []


async def test_looks_hide_filled_fields_and_saved_values(monkeypatch, tools):
    from PIL import Image

    token = _el('entry', 'API key', (100, 200, 400, 230), value='glpat-not-for-browsers-abcdef'[:20])
    after = _snap('s2', 'https://github.com/login', [EMAIL, PASSWORD, token])
    driver = _use(monkeypatch, tools, _Driver(after=after))
    EMAIL.value = 'amos@example.com'
    try:
        await tools.set_text.run(window_id=7, text='{{secret:GITHUB.username}}', element=1)

        def look(window_id, query=None, limit=60, viewer=None):
            driver._snapshots[7] = after
            return Image.new('RGB', (800, 600), (255, 255, 255)), after, list(enumerate(after.elements)), ''

        driver.look = look
        shown = []
        monkeypatch.setattr(tools, 'queue_image', lambda url: shown.append(url) or True)
        monkeypatch.setattr(tools, '_saved_values', lambda: ['glpat-not-for-browsers', 'hunter2-pass'])
        out = (await tools.look_at_window.run(window_id=7)).content
    finally:
        EMAIL.value = None
    # The email field got a saved sign-in; the key field shows the start of a saved value.
    assert 'amos@example.com' not in out and 'glpat' not in out, out
    assert out.count('[saved sign-in hidden]') == 2
    import base64
    import io
    png = Image.open(io.BytesIO(base64.b64decode(shown[0].split(',', 1)[1])))
    assert png.getpixel((250, 115)) == (128, 128, 128)  # covered
    assert png.getpixel((250, 300)) == (255, 255, 255)  # the rest is left alone


def test_redact_hides_cut_off_values():
    from cognitrix.credentials import redact

    secrets = {'ghp_0123456789abcdefghijklmnop': 'GH'}
    assert redact("value='ghp_0123456789abcdefg'", secrets) == "value='[GH hidden]'"
    assert redact('reads ghp_0123456789ab and more', secrets) == 'reads [GH hidden] and more'
    assert redact('ghp_012 is too short to count', secrets) == 'ghp_012 is too short to count'
    assert redact('a ghp_0123456789abcdefghijklmnop b', secrets) == 'a [GH hidden] b'


def test_page_body_is_not_a_field():
    page = _el('document web', 'GitHub', (0, 30, 800, 600))
    snap = _snap('s', 'https://github.com', [page])
    assert ssi._field_at(snap, (500, 400)) is None
    assert ssi._field_at(_snap('s', 'https://github.com', [page, PASSWORD]), (200, 160)) is PASSWORD




# --- stored sign-ins ----------------------------------------------------------------

from tests.test_credentials import _ctx, _user, db  # noqa: E402,F401  (shared vault fixture)


@pytest.mark.usefixtures('db')
async def test_browser_use_is_off_unless_the_person_turns_it_on(monkeypatch):
    from cognitrix.api.routes import credentials as routes
    from cognitrix.models.credential import Credential

    ada = await _user('Ada', 'ada@example.com')
    made = await routes.add_credential(routes.CredentialBody(
        name='gitlab', value='glpat-0123456789abcdef', allowed_hosts=['gitlab.com']), _ctx(ada))
    assert made['use_in_browser'] is False
    row = await Credential.find_one({'name': 'GITLAB'})
    assert row.use_in_browser is False
    turned_on = await routes.update_credential(made['id'], routes.CredentialUpdate(use_in_browser=True), _ctx(ada))
    assert turned_on['use_in_browser'] is True
    assert (await Credential.find_one({'name': 'GITLAB'})).use_in_browser is True

    # Answering an agent's Ask For Sign-in for a website turns it on; for commands only, not.
    from cognitrix.questions import broker
    answered = []
    for name, hosts, browser in (('SITE_LOGIN', ['github.com'], True), ('BOX_ONLY', ['sandbox.invalid'], False)):
        monkeypatch.setattr(broker, 'pending_secret', lambda rid, key, n=name, h=hosts, b=browser: {
            'name': n, 'kind': 'basic', 'hosts': h, 'browser': b})

        async def resolve(*args, **kwargs):
            answered.append(args)

        monkeypatch.setattr(broker, 'resolve_question', resolve)
        await routes.answer_sign_in_request('r1', routes.SignInAnswer(username='me', password='pw-123456'), _ctx(ada))
    assert (await Credential.find_one({'name': 'SITE_LOGIN'})).use_in_browser is True
    assert (await Credential.find_one({'name': 'BOX_ONLY'})).use_in_browser is False
    assert len(answered) == 2


def test_old_rows_read_back_off():
    from cognitrix.models.credential import Credential

    for raw in (None, '', '0', 'false', 0):
        assert Credential(owner_id='u', name='A', use_in_browser=raw).use_in_browser is False
    for raw in ('1', 'true', 1, True):
        assert Credential(owner_id='u', name='A', use_in_browser=raw).use_in_browser is True


async def test_ask_for_sign_in_says_when_it_goes_into_the_browser(monkeypatch):
    from cognitrix.tools import sandbox_work

    asked = []

    async def ask_question(spec):
        asked.append(spec)
        return SimpleNamespace(option_id='saved')

    async def find_row(owner, name):
        return SimpleNamespace(name=name) if asked else None

    async def let(agent, name):
        return None

    monkeypatch.setattr('cognitrix.questions.broker.ask_question', ask_question)
    monkeypatch.setattr('cognitrix.credentials.find_row', find_row)
    monkeypatch.setattr(sandbox_work, '_let_agent_use', let)
    monkeypatch.setattr('cognitrix.tools.utils.current_execution_context',
                        lambda: SimpleNamespace(user_id='u1', agent_id=None, session_id='s1'))
    out = (await sandbox_work.ask_for_sign_in.run(name='github', reason='Sign in to GitHub', kind='basic',
                                                  allowed_hosts=['github.com'])).content
    assert asked[0].secret['browser'] is True and 'sign-in page on github.com' in asked[0].details
    assert 'one field per call' in out
    asked.clear()
    await sandbox_work.ask_for_sign_in.run(name='gh_token', reason='Call the API', allowed_hosts=['api.github.com'])
    assert asked[0].secret['browser'] is False and 'sign-in page' not in asked[0].details
