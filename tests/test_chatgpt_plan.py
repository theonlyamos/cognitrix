"""ChatGPT plan provider: sign-in, token refresh, request shape, streaming."""
import asyncio
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from cognitrix.providers import chatgpt_plan as cp


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    from cognitrix.common import secret_box
    from cognitrix.config import settings

    monkeypatch.setattr(settings, 'workdir', tmp_path)
    monkeypatch.setenv('COGNITRIX_SECRETS_KEY', 'test-secrets-key')
    secret_box.reset_key_cache()
    cp._flows.clear()
    yield tmp_path
    for flow in list(cp._flows.values()):
        cp._close_listener(flow)
    cp._flows.clear()
    secret_box.reset_key_cache()


def _fake_exchange(monkeypatch, *, subject='user-1', scope=cp.SCOPES, seen=None):
    async def token_request(form):
        if seen is not None:
            seen.append(form)
        return {'access_token': 'at-1', 'refresh_token': 'rt-1', 'id_token': 'idt-1',
                'expires_in': 3600, 'scope': scope, 'token_type': 'Bearer'}

    def verify(id_token, client_id, nonce):
        return {'sub': subject, 'email': 'me@example.com', 'name': 'Me'}

    monkeypatch.setattr(cp, '_token_request', token_request)
    monkeypatch.setattr(cp, '_verify_id_token', verify)


async def _callback(redirect_uri: str, **params) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get(redirect_uri, params=params)
    return response.text


# --- config ------------------------------------------------------------------

def test_agent_config_always_points_at_openai():
    from cognitrix.providers.base import LLM

    llm = LLM(provider='chatgpt', model='gpt-6.1-sol', base_url='https://evil.example/v1', api_key='stolen?')
    assert llm.base_url == cp.API_BASE
    assert llm.api_key == cp.TOKEN_PLACEHOLDER
    assert llm.provider == 'chatgpt'


def test_plan_usage_costs_nothing_on_the_usage_page():
    from cognitrix.usage import pricing

    assert pricing.cost({}, 'chatgpt', 'gpt-6.1-sol', {'prompt_tokens': 10, 'completion_tokens': 5}) == 0.0


# --- request shape -------------------------------------------------------------

TOOLS = [{'type': 'function', 'function': {
    'name': 'get_weather', 'description': 'Weather',
    'parameters': {'type': 'object', 'properties': {'city': {'type': 'string'}}}}}]


def test_messages_become_responses_items():
    messages = [
        {'role': 'system', 'content': 'Be terse.'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Look'},
                                     {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA'}}]},
        {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'c1', 'type': 'function', 'function': {'name': 'get_weather', 'arguments': '{"city":"Rome"}'}},
            {'id': 'c2', 'type': 'function', 'function': {'name': 'get_weather', 'arguments': '{}'}},
        ]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': 'Sunny'},
        {'role': 'system', 'content': 'Reply briefly.'},
        {'role': 'tool', 'tool_call_id': 'zz', 'content': 'stray'},
    ]
    instructions, items = cp.convert_messages(messages, with_tools=True)
    assert instructions == 'Be terse.'
    assert items[0]['content'][1] == {'type': 'input_image', 'image_url': 'data:image/png;base64,AA', 'detail': 'auto'}
    calls = [i for i in items if i['type'] == 'function_call']
    assert [c['call_id'] for c in calls] == ['c1', 'c2'] and all(c['namespace'] == cp.NAMESPACE for c in calls)
    outputs = {i['call_id']: i['output'] for i in items if i['type'] == 'function_call_output'}
    assert outputs == {'c1': 'Sunny', 'c2': '(no result was recorded)'}
    # A later system message is a developer message; no system items at all.
    assert any(i.get('role') == 'developer' for i in items)
    assert not any(i.get('role') == 'system' for i in items)
    assert items[-1]['role'] == 'user' and 'stray' in items[-1]['content'][0]['text']


def test_without_tools_earlier_calls_are_text():
    messages = [
        {'role': 'user', 'content': 'hi'},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'c1', 'function': {'name': 'x', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': 'done'},
    ]
    _, items = cp.convert_messages(messages, with_tools=False)
    assert not any(i['type'] in ('function_call', 'function_call_output') for i in items)


def test_params_keep_to_the_plan_route_rules():
    class L:
        model = 'gpt-6.1-sol'

    params = cp.build_params(L(), [{'role': 'user', 'content': 'hi'}], TOOLS, 'high', {'type': 'json_object'})
    assert params['store'] is False and params['stream'] is True
    for banned in ('temperature', 'max_output_tokens', 'metadata', 'previous_response_id', 'user'):
        assert banned not in params
    assert params['tools'][0]['type'] == 'namespace'
    assert params['tools'][0]['tools'][0]['name'] == 'get_weather'
    assert params['reasoning'] == {'summary': 'auto', 'effort': 'high'}
    assert params['text'] == {'format': {'type': 'json_object'}}


# --- sign-in -------------------------------------------------------------------

async def test_first_sign_in_registers_and_saves(workdir, monkeypatch):
    seen = []
    _fake_exchange(monkeypatch, seen=seen)
    flow = await cp.start_sign_in('owner-1')
    query = parse_qs(urlsplit(flow['authorize_url']).query)
    assert query['client_id'] == [cp.DYNAMIC_CLIENT]
    assert query['agent_name_hint'] == ['Cognitrix']
    assert query['ext_agent_host_id'][0].startswith('urn:uuid:')
    assert query['resource'] == [cp.API_BASE]
    assert cp.PLAN_SCOPE in query['scope'][0]
    assert flow['redirect_uri'].startswith('http://127.0.0.1:') and flow['redirect_uri'].endswith('/auth/callback')

    # A stale tab with the wrong state is ignored.
    await _callback(flow['redirect_uri'], code='x', state='nope', client_id='oaiapp_1')
    assert cp.flow_status(flow['flow_id'], 'owner-1')['status'] == 'pending'

    page = await _callback(flow['redirect_uri'], code='code-1', state=query['state'][0], client_id='oaiapp_1')
    assert 'Signed in to ChatGPT' in page
    assert cp.flow_status(flow['flow_id'], 'owner-1') == {'status': 'done', 'error': None}
    assert cp.flow_status(flow['flow_id'], 'someone-else') is None
    assert seen[0]['client_id'] == 'oaiapp_1' and seen[0]['redirect_uri'] == flow['redirect_uri']

    status = cp.status()
    assert status['signed_in'] and status['email'] == 'me@example.com' and status['owner_id'] == 'owner-1'
    assert await cp.access_token() == 'at-1'
    assert 'at-1' not in (workdir / 'chatgpt_plan.json').read_text()  # sealed on disk


async def test_returning_sign_in_reuses_the_issued_client(workdir, monkeypatch):
    _fake_exchange(monkeypatch)
    flow = await cp.start_sign_in('owner-1')
    state = parse_qs(urlsplit(flow['authorize_url']).query)['state'][0]
    await cp.finish_sign_in(flow['flow_id'], {'code': 'c', 'state': state, 'client_id': 'oaiapp_1'})
    host = cp.host_id()

    again = await cp.start_sign_in('owner-1')
    query = parse_qs(urlsplit(again['authorize_url']).query)
    assert query['client_id'] == ['oaiapp_1']
    assert 'agent_name_hint' not in query
    assert query['login_hint'] == ['me@example.com']
    assert query['ext_agent_host_id'] == [host]
    # A different client id in the callback is refused.
    await cp.finish_sign_in(again['flow_id'], {'code': 'c', 'state': query['state'][0], 'client_id': 'oaiapp_2'})
    assert cp.flow_status(again['flow_id'], 'owner-1')['status'] == 'error'

    fresh = await cp.start_sign_in('owner-1', new_account=True)
    assert parse_qs(urlsplit(fresh['authorize_url']).query)['client_id'] == [cp.DYNAMIC_CLIENT]


async def test_pasted_address_finishes_a_remote_sign_in(workdir, monkeypatch):
    _fake_exchange(monkeypatch)
    flow = await cp.start_sign_in('owner-1')
    state = parse_qs(urlsplit(flow['authorize_url']).query)['state'][0]
    with pytest.raises(cp.ChatGPTPlanError):
        await cp.finish_from_pasted_url(flow['flow_id'], 'owner-1', 'https://example.com/?code=1')
    result = await cp.finish_from_pasted_url(
        flow['flow_id'], 'owner-1', f'http://127.0.0.1:1455/auth/callback?code=c&state={state}&client_id=oaiapp_9')
    assert result == {'status': 'done', 'error': None}


async def test_declined_plan_scope_keeps_no_session(workdir, monkeypatch):
    _fake_exchange(monkeypatch, scope='openid profile email offline_access')
    flow = await cp.start_sign_in('owner-1')
    state = parse_qs(urlsplit(flow['authorize_url']).query)['state'][0]
    await cp.finish_sign_in(flow['flow_id'], {'code': 'c', 'state': state, 'client_id': 'oaiapp_1'})
    found = cp.flow_status(flow['flow_id'], 'owner-1')
    assert found['status'] == 'error' and 'plan use was not allowed' in found['error']
    assert not cp.status()['signed_in']
    with pytest.raises(cp.NotSignedIn):
        await cp.access_token()


async def test_access_denied(workdir, monkeypatch):
    flow = await cp.start_sign_in('owner-1')
    state = parse_qs(urlsplit(flow['authorize_url']).query)['state'][0]
    await cp.finish_sign_in(flow['flow_id'], {'error': 'access_denied', 'state': state})
    assert cp.flow_status(flow['flow_id'], 'owner-1')['error'] == 'You declined the ChatGPT sign-in.'


# --- tokens ---------------------------------------------------------------------

def _save_session(**overrides):
    session = {'subject': 's', 'email': 'me@example.com', 'client_id': 'oaiapp_1', 'owner_id': 'owner-1',
               'access_token': 'old', 'refresh_token': 'rt-old', 'expires_at': time.time() - 10,
               'earliest_refresh_at': 0, 'id_token': 'idt'}
    session.update(overrides)
    cp._write({'host_id': 'urn:uuid:x', 'session': session, 'last_subject': 's',
               'registrations': {'s': {'client_id': 'oaiapp_1', 'email': 'me@example.com'}}})


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


async def test_refresh_rotates_tokens(workdir, monkeypatch):
    _save_session()
    sent = []

    def post(url, data=None, **kw):
        sent.append(data)
        return _Resp(200, {'access_token': 'new', 'refresh_token': 'rt-new', 'expires_in': 3600})

    monkeypatch.setattr(httpx, 'post', post)
    assert await cp.access_token() == 'new'
    assert sent[0]['grant_type'] == 'refresh_token' and sent[0]['client_id'] == 'oaiapp_1'
    assert sent[0]['refresh_token'] == 'rt-old' and sent[0]['resource'] == cp.API_BASE
    assert cp._session()['refresh_token'] == 'rt-new'
    assert await cp.access_token() == 'new'  # fresh now: no second refresh
    assert len(sent) == 1


async def test_sign_out_during_refresh_stays_signed_out(workdir, monkeypatch):
    _save_session()

    def post(url, data=None, **kw):
        # The person signs out while the token request is on the wire.
        with cp._lock:
            stored = cp._read()
            stored.pop('session', None)
            cp._write(stored)
        return _Resp(200, {'access_token': 'new', 'refresh_token': 'rt-new', 'expires_in': 3600})

    monkeypatch.setattr(httpx, 'post', post)
    with pytest.raises(cp.NotSignedIn):
        await cp.access_token()
    assert cp._session() is None


async def test_dead_refresh_token_asks_to_sign_in_again(workdir, monkeypatch):
    _save_session()
    monkeypatch.setattr(httpx, 'post', lambda *a, **k: _Resp(400, {'error': 'refresh_token_reused'}))
    with pytest.raises(cp.NotSignedIn):
        await cp.access_token()
    status = cp.status()
    assert not status['signed_in'] and status['needs_sign_in'] == 'expired'
    assert status['previous_email'] == 'me@example.com'


def test_only_the_person_who_connected_it_can_use_it(workdir):
    _save_session(expires_at=time.time() + 3600)
    cp.check_owner('owner-1')
    cp.check_owner(None)
    with pytest.raises(cp.ChatGPTPlanError):
        cp.check_owner('owner-2')


# --- streaming -----------------------------------------------------------------

def _events(*events):
    async def gen():
        for e in events:
            yield e

    async def open_stream(params, client=None):
        return gen()
    return open_stream


async def _collect(params=None):
    out = None
    async for item in cp.stream(None, params or {}):
        out = item
    return out


async def test_stream_yields_text_reasoning_and_tool_calls(monkeypatch):
    monkeypatch.setattr(cp, '_open_stream', _events(
        {'type': 'response.reasoning_summary_text.delta', 'delta': 'Thinking'},
        {'type': 'response.output_text.delta', 'delta': 'Checking'},
        {'type': 'response.output_item.done', 'item': {
            'type': 'function_call', 'name': 'get_weather', 'arguments': '{"city":"Rome"}', 'call_id': 'c1'}},
        {'type': 'response.output_item.done', 'item': {
            'type': 'function_call', 'name': 'get_weather', 'arguments': '{"city":', 'call_id': 'c2'}},
        {'type': 'response.completed', 'response': {'usage': {
            'input_tokens': 50, 'output_tokens': 7, 'input_tokens_details': {'cached_tokens': 20}}}},
    ))
    out = await _collect()
    assert out.error is None
    assert out.reasoning == 'Thinking' and out.llm_response == 'Checking'
    assert out.tool_calls[0] == {'name': 'get_weather', 'arguments': {'city': 'Rome'}, 'tool_call_id': 'c1'}
    assert 'arguments_error' in out.tool_calls[1]
    assert out.finish_reason == 'tool_calls'
    assert out.usage == {'prompt_tokens': 50, 'completion_tokens': 7, 'cached_tokens': 20}


async def test_usage_limit_points_at_chatgpt_settings(monkeypatch):
    monkeypatch.setattr(cp, '_open_stream', _events(
        {'type': 'response.output_text.delta', 'delta': 'Hel'},
        {'type': 'response.failed', 'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}},
    ))
    out = await _collect()
    assert out.error and cp.USAGE_URL in out.error


async def test_stream_cut_off_is_an_error(monkeypatch):
    monkeypatch.setattr(cp, '_open_stream', _events({'type': 'response.output_text.delta', 'delta': 'Hel'}))
    out = await _collect()
    assert out.error and 'stopped before it finished' in out.error


async def test_someone_elses_chat_is_refused(workdir, monkeypatch):
    from cognitrix import usage

    _save_session(expires_at=time.time() + 3600)
    monkeypatch.setattr(cp, '_open_stream', _events({'type': 'response.completed', 'response': {}}))
    token = usage.set_owner('owner-2')
    try:
        out = await _collect()
    finally:
        usage.reset_owner(token)
    assert out.error and 'belongs to the person who connected it' in out.error


async def test_non_streaming_reply_reads_the_stream(monkeypatch):
    monkeypatch.setattr(cp, '_open_stream', _events(
        {'type': 'response.output_text.delta', 'delta': 'Hi there'},
        {'type': 'response.completed', 'response': {'usage': {'input_tokens': 3, 'output_tokens': 2}}},
    ))
    out = await cp.respond(None, {})
    assert out.llm_response == 'Hi there' and out.finish_reason == 'stop' and out.current_chunk == ''


def test_copilot_cli_modes():
    from cognitrix.common import coding_agents as ca

    cli = ca.CLIS['copilot']
    assert cli.build('read_only', None) == ['-s', '--no-color', '--no-ask-user', '--no-auto-update']
    assert cli.build('edit', 'gpt-6.1-sol')[-4:] == ['--allow-tool', 'write', '--model', 'gpt-6.1-sol']
    assert '--allow-all' in cli.build('full', None)
    assert ca.normalise_agent('GitHub Copilot') == 'copilot'


def test_flow_expires(workdir):
    async def run():
        flow = await cp.start_sign_in('owner-1')
        cp._expire(flow['flow_id'])
        return cp.flow_status(flow['flow_id'], 'owner-1')

    assert asyncio.run(run())['status'] == 'error'


async def test_api_key_route_skips_the_plan_checks(workdir, monkeypatch):
    """base.py sends newer OpenAI models here with an API key client."""
    _save_session(owner_id='someone-else')
    seen = []

    class Responses:
        async def create(self, **params):
            seen.append(params)

            async def gen():
                yield {'type': 'response.output_text.delta', 'delta': 'Hi'}
                yield {'type': 'response.completed', 'response': {'usage': {'input_tokens': 1, 'output_tokens': 1}}}
            return gen()

    class Client:
        responses = Responses()

    async def no_token(**kwargs):
        raise AssertionError('the plan token must not be used')

    monkeypatch.setattr(cp, 'access_token', no_token)
    out = await cp.respond(None, {'model': 'gpt-6.1-sol'}, Client())
    assert out.llm_response == 'Hi' and out.error is None
    assert seen == [{'model': 'gpt-6.1-sol'}]
    assert cp._error_message('rate_limit', 'Slow down', Client()) == 'OpenAI request failed (rate_limit): Slow down.'
