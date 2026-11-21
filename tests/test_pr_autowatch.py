"""Pull requests agents open are watched without being asked
(cognitrix/tasks/pr_autowatch.py).

- A GitHub "create pull request" tool, `gh pr create` in Bash, or a coding
  agent saying it opened one all start a watch for the chat that did it.
- A pull request that only shows up in a tool's output (looked up, listed,
  opened long ago) is left alone.
- One watch per pull request; none when the person turned it off for their
  account or for the project.
"""
from datetime import datetime, timedelta

from cognitrix.tasks import pr_autowatch, pr_watch
from cognitrix.tools.utils import ToolOutcome
from tests import test_pr_watch
from tests.test_pr_watch import GitHub, _chat, _github_row

# The fake GitHub and database come from the Watch Pull Request tests.
db = test_pr_watch.db
delivered = test_pr_watch.delivered

URL = 'https://github.com/acme/app/pull/7'


def _fresh(gh, minutes=1):
    gh.pr['created_at'] = (datetime.utcnow() - timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')


def test_finds_pull_requests_tools_opened():
    find = pr_autowatch.opened_pull_requests
    assert find('github__create_pull_request', {}, f'{{"html_url": "{URL}", "number": 7}}') == ([('acme/app', 7)], True)
    assert find('create_pull_request', {'owner': 'acme', 'repo': 'app'}, '{"number": 7}') == ([('acme/app', 7)], True)
    assert find('Run Mcp Tool', {'tool_name': 'create_pull_request'}, URL) == ([('acme/app', 7)], True)
    assert find('Bash', {'command': 'gh pr create --fill'}, f'{URL}\n') == ([('acme/app', 7)], False)
    assert find('Bash Output', {'shell_id': 's1'}, f'Done. I opened a pull request: {URL}') == ([('acme/app', 7)], False)
    assert find('Coding Agent', {'task': 'fix it'}, f'Created PR {URL}/files') == ([('acme/app', 7)], False)
    # Mentioned, looked up or listed: not opened here.
    assert find('Bash', {'command': 'gh pr view 7'}, URL) == ([], False)
    assert find('Web Fetch', {'url': URL}, f'pull request created {URL}') == ([], False)
    assert find('github__list_pull_requests', {}, f'opened a PR {URL}') == ([], False)
    assert not pr_autowatch.maybe_opened('Bash', 'all tests passed')


async def _run(tool, args, text):
    from cognitrix.agents.base import _watch_opened_pull_requests

    class T:
        name = tool

    return await _watch_opened_pull_requests(T(), args, ToolOutcome.success(text))


async def test_gh_pr_create_in_a_chat_starts_a_watch(db, monkeypatch, delivered):
    gh = GitHub(monkeypatch)
    _fresh(gh)
    await _github_row()
    session = await _chat()

    outcome = await _run('Bash', {'command': 'gh pr create --fill'}, URL)
    assert outcome.text.startswith(URL)
    assert 'Cognitrix is now watching acme/app#7 for you' in outcome.text
    watches = await pr_watch.PullRequestWatch.find({})
    assert len(watches) == 1
    watch = watches[0]
    assert watch.auto and watch.session_id == str(session.id) and watch.status == 'watching'
    assert 'You opened this pull request' in watch.instructions
    assert pr_watch.public_watch(watch)['auto'] is True

    # Seeing it again (a later Bash Output, a second agent) adds nothing.
    again = await _run('Bash Output', {}, f'Created pull request {URL}')
    assert again.text == f'Created pull request {URL}'
    assert len(await pr_watch.PullRequestWatch.find({})) == 1

    # News comes back to the chat like any watch, and stops on merge.
    gh.pr.update(state='closed', merged=True, merged_by={'login': 'ann'})
    t0 = datetime.utcnow()
    assert await pr_watch.poll_pr_watches(t0 + timedelta(minutes=3)) == 1
    assert delivered[-1]['session'] == str(session.id) and 'Merged by ann.' in delivered[-1]['text']


async def test_an_old_pull_request_is_left_alone(db, monkeypatch):
    gh = GitHub(monkeypatch)
    _fresh(gh, minutes=24 * 60)
    await _github_row()
    await _chat()
    outcome = await _run('Bash', {'command': 'gh pr create || gh pr view --web'}, URL)
    assert outcome.text == URL
    assert await pr_watch.PullRequestWatch.find({}) == []


async def test_a_create_tool_says_when_github_is_not_connected(db, monkeypatch):
    GitHub(monkeypatch)
    await _chat()
    outcome = await _run('create_pull_request', {}, URL)
    assert 'GitHub is not connected' in outcome.text
    # A guess from a command stays quiet instead.
    assert (await _run('Bash', {'command': 'gh pr create'}, URL)).text == URL


async def test_turned_off_for_the_account(db, monkeypatch):
    gh = GitHub(monkeypatch)
    _fresh(gh)
    await _github_row()
    await _chat()
    assert await pr_watch.auto_watch_on('u1')
    await pr_watch.set_auto_watch('u1', False)
    assert not await pr_watch.auto_watch_on('u1')
    assert (await _run('create_pull_request', {}, URL)).text == URL
    assert await pr_watch.PullRequestWatch.find({}) == []


async def test_watch_in_a_project_belongs_to_it_unless_turned_off(db, monkeypatch):
    from cognitrix.projects import context as project_context
    from cognitrix.projects import store

    gh = GitHub(monkeypatch)
    _fresh(gh)
    await _github_row()
    await _chat()
    project = await store.create('u1', {'name': 'Web app'})
    assert project.watch_prs and store.view(project)['watch_prs'] is True

    token = project_context.set_project(project_context.TurnProject.of(project))
    try:
        await store.update(project, {'watch_prs': False})
        assert (await _run('create_pull_request', {}, URL)).text == URL
        assert await pr_watch.PullRequestWatch.find({}) == []

        await store.update(project, {'watch_prs': True})
        outcome = await _run('create_pull_request', {}, URL)
        assert 'now watching acme/app#7' in outcome.text
    finally:
        project_context.reset(token)
    watch = (await pr_watch.PullRequestWatch.find({}))[0]
    assert watch.project_id == str(project.id)
    assert 'the project "Web app"' in watch.instructions


async def test_background_create_and_wrapped_create_tool(db, monkeypatch):
    gh = GitHub(monkeypatch)
    _fresh(gh)
    await _github_row()
    await _chat()
    pr_autowatch._creating_shells.clear()
    # gh pr create in the background: the link only shows in Bash Output.
    started = await _run('Bash', {'command': 'gh pr create --fill', 'run_in_background': True},
                         "Started. Read its output with Bash Output (shell_id='bg7') and stop it with Kill Shell.")
    assert 'watching' not in started.text
    outcome = await _run('Bash Output', {'shell_id': 'bg7'}, f'status: done\n{URL}')
    assert 'now watching acme/app#7' in outcome.text


async def test_run_mcp_tool_create_without_a_link(db, monkeypatch):
    gh = GitHub(monkeypatch)
    _fresh(gh)
    await _github_row()
    await _chat()
    args = {'server': 'GitHub', 'tool_name': 'create_pull_request', 'arguments': {'owner': 'acme', 'repo': 'app'}}
    assert pr_autowatch.maybe_opened('Run Mcp Tool', '{"number": 7}', args)
    outcome = await _run('Run Mcp Tool', args, '{"number": 7}')
    assert 'now watching acme/app#7' in outcome.text
