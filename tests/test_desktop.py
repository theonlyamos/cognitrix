"""Desktop app engine: frozen worker command, inbox and status routes, and the
shell tool's working-folder limit."""
import sys
from types import SimpleNamespace

import pytest


def test_worker_command_reuses_the_frozen_executable(monkeypatch):
    from cognitrix import celery_worker

    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', r'C:\Program Files\Cognitrix\engine\cognitrix-engine.exe')
    cmd = celery_worker.worker_command()
    assert cmd[:2] == [sys.executable, 'worker']
    assert '--pool=solo' in cmd and '-m' not in cmd


def test_worker_command_uses_celery_module_when_not_frozen(monkeypatch):
    from cognitrix import celery_worker

    monkeypatch.delattr(sys, 'frozen', raising=False)
    cmd = celery_worker.worker_command()
    assert cmd[1:4] == ['-m', 'celery', '-A']


def test_desktop_cli_routes_worker_args_to_celery(monkeypatch):
    from cognitrix import desktop

    seen = {}
    monkeypatch.setattr(desktop, 'run_worker', lambda args: seen.setdefault('args', args) and 0)
    desktop.main(['worker', '--pool=solo', '--loglevel=info'])
    assert seen['args'] == ['--pool=solo', '--loglevel=info']

    args = desktop.build_parser().parse_args(['engine', '--port', '48299', '--parent-pid', '42'])
    assert (args.host, args.port, args.parent_pid) == ('127.0.0.1', 48299, 42)


def test_engine_environment_defaults(tmp_path, monkeypatch):
    from cognitrix import desktop

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('COGNITRIX_TOOLS_ROOT', raising=False)
    monkeypatch.delenv('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', raising=False)
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    folder = tmp_path / 'work'
    folder.mkdir()
    desktop._prepare_environment(str(folder))
    import os
    assert os.environ['COGNITRIX_TOOLS_ROOT'] == str(folder)
    assert os.environ['COGNITRIX_DESKTOP'] == '1'
    assert int(os.environ['JWT_ACCESS_TOKEN_EXPIRE_MINUTES']) >= 60 * 24
    assert os.getcwd() == str(folder)
    # The settings object other modules already hold sees the new values.
    from cognitrix.config import settings
    assert settings.tools_root == folder.resolve()
    assert settings.jwt_access_token_expire_minutes >= 60 * 24

    monkeypatch.undo()
    settings.__init__()


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'desktop.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    return db_file


@pytest.mark.asyncio
async def test_inbox_lists_only_your_pending_approvals(db):
    from cognitrix.api.routes.system import inbox
    from cognitrix.tasks import approvals
    from cognitrix.tasks.base import Task

    task = Task(title='Audit API key handling', description='x')
    await task.save()
    mine = approvals.TaskApproval(run_id='r1', task_id=task.id, step_index=4, user_id='me', tool_name='Bash',
                                  params={'command': 'git push'})
    await mine.save()
    await approvals.TaskApproval(run_id='r1', task_id=task.id, user_id='me', tool_name='Write',
                                 status=approvals.APPROVED).save()
    await approvals.TaskApproval(run_id='r2', task_id=task.id, user_id='someone-else', tool_name='Bash').save()

    ctx = SimpleNamespace(user=SimpleNamespace(id='me', email='me@example.com'))
    result = await inbox(ctx)

    assert result['count'] == 1
    item = result['items'][0]
    assert item['kind'] == 'approval' and item['id'] == mine.id
    assert item['task_title'] == 'Audit API key handling'
    assert item['params'] == {'command': 'git push'}


@pytest.mark.asyncio
async def test_system_info_reports_folders(tmp_path, monkeypatch):
    from cognitrix.api.routes.system import system_info
    from cognitrix.config import settings

    monkeypatch.setattr(settings, 'tools_root', tmp_path)
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    info = await system_info(SimpleNamespace(user=SimpleNamespace(id='me')))
    assert info['desktop'] is True
    assert info['working_folder'] == str(tmp_path)
    assert info['task_queue'] in ('filesystem', 'redis')


@pytest.mark.asyncio
async def test_bash_refuses_a_working_dir_outside_the_working_folder(tmp_path, monkeypatch):
    from cognitrix.common.process_security import HostProcessMode
    from cognitrix.config import settings
    from cognitrix.tools.misc import bash
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

    root = tmp_path / 'work'
    (root / 'sub').mkdir(parents=True)
    (root / 'sub' / 'marker.txt').write_text('x')
    monkeypatch.setattr(settings, 'tools_root', root.resolve())
    token = set_execution_context(ToolExecutionContext(host_process_mode=HostProcessMode.TRUSTED_LOCAL))
    try:
        refused = await bash.run(command='ls', working_dir=str(tmp_path))
        escaped = await bash.run(command='ls', working_dir='../')
        allowed = await bash.run(command='ls', working_dir='sub')
    finally:
        reset_execution_context(token)

    assert 'Command refused' in refused.content and 'outside the working folder' in refused.content
    assert 'Command refused' in escaped.content
    assert 'marker.txt' in allowed.content
