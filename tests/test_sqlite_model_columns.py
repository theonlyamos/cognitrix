import sqlite3

import pytest


@pytest.mark.asyncio
async def test_existing_sqlite_db_gets_new_model_columns(monkeypatch, tmp_path):
    """An upgraded app adds model fields its old database lacks.

    0.5.8 added Session.token_usage and Agent.code_on_my_computer without a
    migration entry, so every chat save failed on existing databases.
    """
    from cognitrix import config

    db = tmp_path / 'old.db'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE sessions (chat TEXT, agent_id TEXT, id TEXT PRIMARY KEY)')
    con.execute('CREATE TABLE agents (name TEXT, llm TEXT, id TEXT PRIMARY KEY)')
    con.commit()
    con.close()

    monkeypatch.setattr(config.settings, 'db_type', 'sqlite')
    monkeypatch.setattr(config.settings, 'db_name', str(db))
    await config.initialize_database()

    con = sqlite3.connect(db)
    sessions = {r[1] for r in con.execute('PRAGMA table_info(sessions)')}
    agents = {r[1] for r in con.execute('PRAGMA table_info(agents)')}
    con.close()
    assert {'token_usage', 'run_in_sandbox', 'project_id'} <= sessions
    assert {'code_on_my_computer', 'run_in_sandbox', 'tool_mode'} <= agents
