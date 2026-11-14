"""What's new: release notes shown once per user after the app updates."""
from pathlib import Path

import pytest


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



@pytest.fixture
def notes(tmp_path, monkeypatch):
    """Bundled notes for 0.5.3-0.5.5 and a running version of 0.5.5."""
    from cognitrix.api.routes import whats_new

    folder = tmp_path / 'release_notes'
    folder.mkdir()
    for v in ('0.5.3', '0.5.4', '0.5.5', '0.6.0'):
        (folder / f'{v}.md').write_text(
            f'# Cognitrix v{v}\n\nCovers PRs #1 to #2, merged since before.\n\n## Highlights\n- thing in {v}\n',
            encoding='utf-8',
        )
    monkeypatch.setattr(whats_new, 'NOTES_DIR', folder)
    monkeypatch.setattr(whats_new, 'VERSION', '0.5.5')
    whats_new.all_releases.cache_clear()
    yield whats_new
    whats_new.all_releases.cache_clear()


async def _agent(owner_id, name='Assistant'):
    from cognitrix.agents import Agent
    from cognitrix.providers.base import LLM

    agent = Agent(name=name, owner_id=owner_id, system_prompt='hi', llm=LLM(provider='anthropic', model='claude-sonnet-5-5', api_key='sk-ant-test-123456'))
    await agent.save()
    return agent


def test_parse_version():
    from cognitrix.api.routes.whats_new import parse_version

    assert parse_version('0.5.10') > parse_version('0.5.9')
    assert parse_version('v0.6') == (0, 6, 0)
    assert parse_version('0.5.6+local') == (0, 5, 6)
    assert parse_version('nonsense') is None
    assert parse_version(None) is None


async def test_new_account_never_sees_it_and_is_marked_seen(db, notes):
    from cognitrix.models import User

    user = await _user()
    result = await notes.whats_new(_ctx(user))
    assert result['show'] is False
    fresh = await User.find_one({'id': user.id})
    assert fresh.whats_new_seen == '0.5.5'


async def test_existing_user_from_before_the_feature_sees_current_release_once(db, notes):
    from cognitrix.models import User

    user = await _user()
    await _agent(user.id)
    result = await notes.whats_new(_ctx(user))
    assert result['show'] is True
    assert [r['version'] for r in result['releases']] == ['0.5.5']
    body = result['releases'][0]['notes']
    assert body.startswith('- thing in 0.5.5')  # title, "Covers PRs" and Highlights dropped
    assert 'Covers PRs' not in body
    assert result['releases'][0]['url'].endswith('/tag/v0.5.5')

    await notes.mark_seen(_ctx(user))
    fresh = await User.find_one({'id': user.id})
    assert (await notes.whats_new(_ctx(fresh)))['show'] is False


async def test_skipped_versions_are_all_listed_newest_first(db, notes):
    from cognitrix.models import User

    user = await _user()
    await _agent(user.id)
    await User.update_one({'id': user.id}, {'whats_new_seen': '0.5.2'})
    fresh = await User.find_one({'id': user.id})
    result = await notes.whats_new(_ctx(fresh))
    # 0.6.0 is newer than the running version, so it isn't shown yet.
    assert [r['version'] for r in result['releases']] == ['0.5.5', '0.5.4', '0.5.3']


async def test_downgrade_or_dev_build_shows_nothing(db, notes, monkeypatch):
    from cognitrix.models import User

    user = await _user()
    await _agent(user.id)
    await User.update_one({'id': user.id}, {'whats_new_seen': '0.6.0'})
    fresh = await User.find_one({'id': user.id})
    assert (await notes.whats_new(_ctx(fresh)))['show'] is False

    # A version with no bundled notes: no window, and nothing recorded.
    monkeypatch.setattr(notes, 'VERSION', '0.5.6')
    await User.update_one({'id': user.id}, {'whats_new_seen': '0.5.5'})
    fresh = await User.find_one({'id': user.id})
    assert (await notes.whats_new(_ctx(fresh)))['show'] is False
    assert (await User.find_one({'id': user.id})).whats_new_seen == '0.5.5'

    # Even someone behind on older releases gets no window on a dev build.
    monkeypatch.setattr(notes, 'VERSION', '0.5.6.dev1')
    await User.update_one({'id': user.id}, {'whats_new_seen': '0.5.3'})
    fresh = await User.find_one({'id': user.id})
    assert (await notes.whats_new(_ctx(fresh)))['show'] is False


async def test_offers_missing_starter_agents(db, notes):
    user = await _user()
    await _agent(user.id)
    await _agent(user.id, name='Researcher')
    result = await notes.whats_new(_ctx(user))
    offered = [a['name'] for a in result['starter_team']]
    assert 'Researcher' not in offered
    assert {'Coder', 'Writer', 'Reviewer', 'Analyst'} <= set(offered)


async def test_no_starter_offer_without_an_assistant_to_lead(db, notes):
    user = await _user()
    await _agent(user.id, name='Coder')  # only a specialist, no assistant
    result = await notes.whats_new(_ctx(user))
    assert result['starter_team'] == []


async def test_notes_menu_lists_recent_releases(db, notes):
    user = await _user()
    result = await notes.recent_notes(_ctx(user))
    assert [r['version'] for r in result['releases']] == ['0.5.5', '0.5.4', '0.5.3']


def test_current_version_has_bundled_notes():
    """Every version bump must add cognitrix/release_notes/<version>.md (the
    GitHub release notes), or users who update see no What's new window."""
    from cognitrix.config import VERSION

    notes = Path(__file__).resolve().parent.parent / 'cognitrix' / 'release_notes' / f'{VERSION}.md'
    assert notes.is_file(), f'Add release notes for {VERSION} at {notes}'
