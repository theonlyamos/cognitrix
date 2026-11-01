"""First-run setup: each new user gets their own default assistant."""
import pytest
from fastapi import HTTPException


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'onboarding.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()

    for name in ('ANTHROPIC', 'OPENAI', 'GOOGLE', 'OPENROUTER', 'GROQ', 'CEREBRAS'):
        monkeypatch.delenv(f'{name}_API_KEY', raising=False)
    from cognitrix.config import settings
    for attr in ('anthropic_api_key', 'openai_api_key', 'google_api_key', 'groq_api_key'):
        monkeypatch.setattr(settings, attr, '', raising=False)
    from cognitrix.providers.base import LLMManager
    monkeypatch.setattr(LLMManager, '_llm_cache', {}, raising=False)
    yield


async def _user(name='Ada', email='ada@example.com'):
    from cognitrix.models import User

    user = User(name=name, email=email, password='x' * 20)
    await user.save()
    return await User.find_one({'email': email})


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


async def test_new_user_needs_setup_and_gets_suggested_provider_with_key(db, monkeypatch):
    from cognitrix.api.routes import onboarding

    monkeypatch.setenv('GROQ_API_KEY', 'gsk-test-key-123456')
    status = await onboarding.onboarding_status(_ctx(await _user()))

    assert status['needs_setup'] is True
    assert status['suggested']['provider'] == 'groq'
    groq = next(p for p in status['providers'] if p['id'] == 'groq')
    assert groq['has_key'] is True
    # Never leak the key itself.
    assert 'gsk-test-key' not in repr(status)


async def test_create_assistant_owns_it_and_finishes_setup(db, monkeypatch):
    from cognitrix.agents import Agent
    from cognitrix.api.routes import onboarding
    from cognitrix.models import User

    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-123456')
    user = await _user()
    result = await onboarding.create_default_assistant(
        onboarding.AssistantSetup(name='  Jarvis ', provider='anthropic', model='claude-sonnet-5-5'),
        _ctx(user),
    )

    agent = await Agent.find_one({'id': result['agent']['id']})
    assert agent.name == 'Jarvis'
    assert agent.owner_id == user.id
    assert agent.llm.provider == 'anthropic'
    assert agent.llm.model == 'claude-sonnet-5-5'
    assert "Ada's personal assistant" in agent.system_prompt
    assert '{name}' in agent.system_prompt  # follows renames
    assert result['agent']['llm']['api_key'] != 'sk-ant-test-123456'

    fresh = await User.find_one({'id': user.id})
    assert fresh.onboarded_at
    assert (await onboarding.onboarding_status(_ctx(fresh)))['needs_setup'] is False
    with pytest.raises(HTTPException) as exc:
        await onboarding.create_default_assistant(
            onboarding.AssistantSetup(provider='anthropic', model='claude-sonnet-5-5'), _ctx(fresh)
        )
    assert exc.value.status_code == 409


async def test_create_assistant_needs_a_key_unless_one_is_pasted(db):
    from cognitrix.api.routes import onboarding

    user = await _user()
    with pytest.raises(HTTPException) as exc:
        await onboarding.create_default_assistant(
            onboarding.AssistantSetup(provider='openai', model='gpt-4o'), _ctx(user)
        )
    assert exc.value.status_code == 400

    result = await onboarding.create_default_assistant(
        onboarding.AssistantSetup(provider='openai', model='gpt-4o', api_key='sk-mine-123456'),
        _ctx(user),
    )
    assert result['agent']['name'] == 'Assistant'


async def test_unknown_provider_is_refused(db):
    from cognitrix.api.routes import onboarding

    with pytest.raises(HTTPException) as exc:
        await onboarding.create_default_assistant(
            onboarding.AssistantSetup(provider='evil', model='x'), _ctx(await _user())
        )
    assert exc.value.status_code == 400


async def test_skip_still_gives_a_user_with_no_agents_an_assistant(db):
    from cognitrix.agents import Agent
    from cognitrix.api.routes import onboarding
    from cognitrix.models import User

    user = await _user()
    result = await onboarding.skip_setup(_ctx(user))
    assert result['agent'] is not None
    assert (await Agent.find_one({'id': result['agent']['id']})).owner_id == user.id
    assert (await User.find_one({'id': user.id})).onboarded_at


async def test_existing_users_with_agents_are_not_asked(db):
    from cognitrix.agents import Agent
    from cognitrix.api.routes import onboarding
    from cognitrix.providers.base import LLM

    user = await _user()
    await Agent(name='Mine', llm=LLM(provider='ollama'), system_prompt='hi', owner_id=user.id).save()
    assert (await onboarding.onboarding_status(_ctx(user)))['needs_setup'] is False


async def test_braces_in_a_user_name_do_not_become_placeholders():
    from cognitrix.agents.templates import default_assistant_prompt

    prompt = default_assistant_prompt('{tools}')
    assert '(tools)' in prompt and '{tools}' not in prompt
    assert 'the user' in default_assistant_prompt('')
