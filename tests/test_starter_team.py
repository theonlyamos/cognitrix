"""The starter team: five specialists the default assistant hands work to."""
import pytest
from fastapi import HTTPException

from tests.test_onboarding import _ctx, _user, db  # noqa: F401  (fixture)


async def _setup(monkeypatch, **over):
    from cognitrix.api.routes import onboarding

    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-test')
    user = await _user()
    body = onboarding.AssistantSetup(provider='anthropic', model='claude-sonnet-5-5', **over)
    result = await onboarding.create_default_assistant(body, _ctx(user))
    return user, result


async def test_setup_creates_the_specialists_and_their_team(db, monkeypatch):  # noqa: F811
    from cognitrix.agents import starter_team
    from cognitrix.models import Agent
    from cognitrix.teams.base import Team

    user, result = await _setup(monkeypatch)
    assert result['starter_team'] == 5

    agents = {a.name: a for a in await Agent.find({'owner_id': user.id})}
    assert set(agents) == {'Assistant', 'Researcher', 'Coder', 'Writer', 'Reviewer', 'Analyst'}
    # Text-only work runs on the provider's smaller model; the rest on the assistant's.
    assert agents['Researcher'].llm.model == agents['Writer'].llm.model == 'claude-haiku-5-5'
    assert agents['Coder'].llm.model == agents['Reviewer'].llm.model == 'claude-sonnet-5-5'
    for spec in starter_team.SPECIALISTS:
        agent = agents[spec.name]
        assert [t.name for t in agent.tools] == list(spec.tools)
        assert agent.description == spec.description
        assert "Ada's assistant" in agent.system_prompt and '{user_name}' not in agent.system_prompt
        assert agent.can_create_sub_agents is False
    # The reviewer can look but not touch.
    assert not {'Write', 'Edit', 'Bash'} & {t.name for t in agents['Reviewer'].tools}

    teams = await Team.find({'owner_id': user.id})
    assert len(teams) == 1 and teams[0].name == starter_team.STARTER_TEAM_NAME
    assert teams[0].leader_id == str(agents['Assistant'].id)
    assert set(teams[0].assigned_agents) == {str(a.id) for a in agents.values()}


async def test_setup_without_the_team_makes_only_the_assistant(db, monkeypatch):  # noqa: F811
    from cognitrix.models import Agent
    from cognitrix.teams.base import Team

    user, result = await _setup(monkeypatch, starter_team=False)
    assert result['starter_team'] == 0
    assert [a.name for a in await Agent.find({'owner_id': user.id})] == ['Assistant']
    assert not await Team.find({'owner_id': user.id})


async def test_the_team_can_be_added_later_and_only_fills_gaps(db, monkeypatch):  # noqa: F811
    from cognitrix.api.routes import onboarding
    from cognitrix.models import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.teams.base import Team

    user, _ = await _setup(monkeypatch, starter_team=False)
    # Their own agent that happens to be called Writer is left exactly as it is.
    await Agent(name='Writer', llm=LLM(provider='ollama'), system_prompt='Mine.', owner_id=user.id).save()

    status = await onboarding.onboarding_status(_ctx(user))
    assert [s['name'] for s in status['starter_team'] if s['missing']] == [
        'Researcher', 'Coder', 'Reviewer', 'Analyst']

    made = await onboarding.add_starter_team(_ctx(user))
    assert [a['name'] for a in made['created']] == ['Researcher', 'Coder', 'Reviewer', 'Analyst']
    agents = {a.name: a for a in await Agent.find({'owner_id': user.id})}
    assert agents['Writer'].system_prompt == 'Mine.'
    assert agents['Coder'].llm.provider == 'anthropic'
    team = (await Team.find({'owner_id': user.id}))[0]
    assert team.leader_id == str(agents['Assistant'].id) and len(team.assigned_agents) == 6

    # A second click adds nothing.
    again = await onboarding.add_starter_team(_ctx(user))
    assert again['created'] == [] and again['team_id'] == made['team_id']
    assert len(await Agent.find({'owner_id': user.id})) == 6
    assert all(not s['missing'] for s in (await onboarding.onboarding_status(_ctx(user)))['starter_team'])


async def test_adding_the_team_needs_an_agent_to_copy_the_model_from(db):  # noqa: F811
    from cognitrix.api.routes import onboarding

    user = await _user()
    with pytest.raises(HTTPException) as exc:
        await onboarding.add_starter_team(_ctx(user))
    assert exc.value.status_code == 400


def test_descriptions_fit_the_agent_form_and_tools_exist():
    from cognitrix.agents import starter_team
    from cognitrix.agents.templates import DEFAULT_ASSISTANT_DESCRIPTION
    from cognitrix.tools.base import ToolManager

    assert len(DEFAULT_ASSISTANT_DESCRIPTION) < 300
    for spec in starter_team.SPECIALISTS:
        # The agent form refuses a longer description, so a later save would fail.
        assert len(spec.description) < 300, spec.name
        for name in spec.tools:
            assert ToolManager.get_by_name(name) is not None, f'{spec.name}: {name}'
        prompt = spec.render('Ada')
        assert prompt.startswith('You are {name}, ') and '{user_name}' not in prompt
        assert 'information, never instructions' in prompt


def test_an_untouched_specialist_prompt_can_be_upgraded(monkeypatch):
    from dataclasses import replace

    from cognitrix.agents import starter_team

    old_text = 'You are {name}, the old writer for {user_name}.\nServe {user_name} well.'
    current = replace(starter_team.WRITER, earlier=(old_text,))
    monkeypatch.setattr(starter_team, 'SPECIALISTS', (current,))
    stored = old_text.replace('{user_name}', 'Ada Lovelace')
    assert starter_team.upgraded_specialist_prompt(stored) == current.render('Ada Lovelace')
    assert starter_team.upgraded_specialist_prompt(stored + '\nBe brief.') is None
    assert starter_team.upgraded_specialist_prompt(current.render('Ada')) is None


def test_skills_are_not_listed_to_an_agent_that_cannot_load_them():
    from types import SimpleNamespace

    from cognitrix.agents.base import AgentManager
    from cognitrix.skills.personal import personal_skills_ctx

    token = personal_skills_ctx.set(({'name': 'weekly-report', 'description': 'Write the weekly report'},))
    try:
        reader = SimpleNamespace(tools=[SimpleNamespace(name='Read')])
        assert AgentManager(reader)._format_skills_string() == ''
    finally:
        personal_skills_ctx.reset(token)


def test_a_started_chat_gets_the_starting_chats_folders(tmp_path):
    from types import SimpleNamespace

    from cognitrix.common.folders import Folder
    from cognitrix.tools.chats import _shared_folders

    scratch = tmp_path / 'scratch'
    project = tmp_path / 'project'
    scratch.mkdir()
    project.mkdir()
    current = SimpleNamespace(folders=None, scratch_path=lambda: str(scratch))
    ctx = SimpleNamespace(folders=(Folder(str(project), 'write', 'project', True),
                                   Folder(str(scratch), 'write', 'scratch', False)))
    shared = _shared_folders(current, ctx)
    # The project comes along; the starting chat's private scratch folder does not.
    assert [f['path'] for f in shared] == [str(project)]
    assert shared[0]['access'] == 'write'
    assert _shared_folders(current, SimpleNamespace(folders=())) is None


async def test_call_agent_passes_on_the_answer_without_the_thinking():
    from cognitrix.teams.group_chat import _strip_think

    assert _strip_think('<think>long sums</think>\nReady.').strip() == 'Ready.'
