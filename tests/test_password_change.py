"""Changing a password in the app, and resetting a forgotten one from the command line."""
import time

import pytest
from fastapi import HTTPException

from tests.test_session_ownership_routes import _initialize_routes_db


async def _user(password='old-password'):
    from cognitrix.common.utils import Utils
    from cognitrix.models import User

    await User._create_table_async()
    user = User(name='Ada', email='ada@example.com', password=Utils.hash_password(password))
    await user.save()
    return await User.find_one({'email': 'ada@example.com'})


def _ctx(user):
    from cognitrix.common.security import AuthContext
    return AuthContext(user=user)


async def test_change_needs_the_current_password_and_signs_out_other_devices(tmp_path):
    from cognitrix.api.routes import auth as routes
    from cognitrix.common.security import authenticate
    from cognitrix.models import User

    await _initialize_routes_db(tmp_path)
    user = await _user()

    for body in (
        routes.PasswordChange(current_password='wrong', new_password='new-password-1'),
        routes.PasswordChange(current_password='old-password', new_password='short'),
        routes.PasswordChange(current_password='old-password', new_password='old-password'),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await routes.change_password(body, _ctx(user))
        assert exc_info.value.status_code == 400

    before = int(time.time())
    result = await routes.change_password(
        routes.PasswordChange(current_password='old-password', new_password='new-password-1'), _ctx(user))
    assert result['access_token']
    assert not await authenticate('ada@example.com', 'old-password')
    assert await authenticate('ada@example.com', 'new-password-1')
    assert (await User.find_one({'email': 'ada@example.com'})).tokens_valid_after >= before


async def test_command_line_reset(tmp_path):
    from cognitrix.cli.handlers import set_account_password
    from cognitrix.common.security import authenticate

    await _initialize_routes_db(tmp_path)
    await _user()
    assert 'No account' in await set_account_password('nobody@example.com', 'new-password-1')
    assert 'at least 8' in await set_account_password('ada@example.com', 'short')
    assert await set_account_password(' ada@example.com ', 'new-password-1') is None
    assert await authenticate('ada@example.com', 'new-password-1')
