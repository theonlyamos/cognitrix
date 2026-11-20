"""Per-device sign-ins and signing in a new device with a code (cognitrix/signins.py)."""
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_session_ownership_routes import _initialize_routes_db

CHROME_WIN = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'
SAFARI_IPHONE = ('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 '
                 '(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1')


@pytest.fixture
async def world(tmp_path, monkeypatch):
    from cognitrix import signins
    from cognitrix.common.utils import Utils
    from cognitrix.models import User
    from cognitrix.models.sign_in import SignIn, SignInCode

    monkeypatch.delenv('COGNITRIX_CODE_SIGNIN', raising=False)
    await _initialize_routes_db(tmp_path)
    for model in (User, SignIn, SignInCode):
        await model._create_table_async()
    try:
        from cognitrix.tasks.automation import AgentNotification
        await AgentNotification._create_table_async()
    except Exception:  # noqa: BLE001
        pass
    signins._checks.clear()
    signins._tries.clear()
    await User(name='Ada', email='ada@example.com', password=Utils.hash_password('old-password')).save()
    ada = await User.find_one({'email': 'ada@example.com'})
    return SimpleNamespace(ada=ada)


def _request(ua=CHROME_WIN, ip='41.66.10.20'):
    return SimpleNamespace(headers={'user-agent': ua}, client=SimpleNamespace(host=ip),
                           query_params={}, base_url='http://testserver/')


async def _ctx(token):
    from cognitrix.common.security import _resolve_jwt
    return await _resolve_jwt(token)


async def _login(ua=CHROME_WIN):
    import json

    from cognitrix.api.routes import auth
    res = await auth.login(auth.LoginForm(username='ada@example.com', password='old-password'), _request(ua))
    return json.loads(res.body)['access_token']


def test_device_names():
    from cognitrix.signins import device_name, mask_address
    assert device_name(CHROME_WIN) == 'Chrome on Windows'
    assert device_name(SAFARI_IPHONE) == 'Safari on iPhone'
    assert device_name('') == 'Unknown device'
    assert mask_address('41.66.10.20') == '41.66.x.x'


async def test_each_device_signs_out_on_its_own(world):
    from cognitrix.api.routes import sign_ins

    laptop = await _login()
    phone = await _login(SAFARI_IPHONE)
    laptop_ctx, phone_ctx = await _ctx(laptop), await _ctx(phone)
    assert laptop_ctx.sid and phone_ctx.sid and laptop_ctx.sid != phone_ctx.sid

    listed = (await sign_ins.list_sign_ins(laptop_ctx))['sign_ins']
    assert [(s['name'], s['current'], s['method']) for s in listed] == [
        ('Chrome on Windows', True, 'password'), ('Safari on iPhone', False, 'password')]

    await sign_ins.end_sign_in(phone_ctx.sid, laptop_ctx)
    with pytest.raises(HTTPException):
        await _ctx(phone)
    assert (await _ctx(laptop)).sid == laptop_ctx.sid
    with pytest.raises(HTTPException):  # not twice, and not someone else's
        await sign_ins.end_sign_in(phone_ctx.sid, laptop_ctx)


async def test_sign_out_others_keeps_this_device(world):
    from cognitrix.api.routes import sign_ins

    here, other1, other2 = await _login(), await _login(SAFARI_IPHONE), await _login()
    ctx = await _ctx(here)
    assert (await sign_ins.sign_out_others(ctx))['signed_out'] == 2
    for token in (other1, other2):
        with pytest.raises(HTTPException):
            await _ctx(token)
    await _ctx(here)
    await sign_ins.sign_out_here(ctx)
    with pytest.raises(HTTPException):
        await _ctx(here)


async def test_older_tokens_still_work_and_get_a_row_on_refresh(world):
    from cognitrix.api.routes import auth, sign_ins
    from cognitrix.common.security import create_access_token

    old = create_access_token({'sub': 'ada@example.com'})
    ctx = await _ctx(old)
    assert ctx.sid == ''
    fresh = (await auth.refresh(ctx, _request()))['access_token']
    new_ctx = await _ctx(fresh)
    assert new_ctx.sid
    assert [s['method'] for s in (await sign_ins.list_sign_ins(new_ctx))['sign_ins']] == ['earlier']


async def test_a_code_signs_in_one_new_device(world):
    from cognitrix.api.routes import sign_ins

    laptop = await _ctx(await _login())
    made = await sign_ins.make_sign_in_code(_request(), laptop)
    code = made['code']
    assert len(code) == 9 and made['expires_in'] == 300
    assert (await sign_ins.sign_in_code_status(code, laptop))['status'] == 'waiting'

    out = await sign_ins.code_sign_in(
        sign_ins.CodeSignIn(code=code.lower().replace('-', ' ')), _request(SAFARI_IPHONE, '10.0.0.5'))
    assert out['user']['email'] == 'ada@example.com' and 'password' not in out['user']
    phone = await _ctx(out['access_token'])

    status = await sign_ins.sign_in_code_status(code, laptop)
    assert status['status'] == 'used' and status['sign_in']['name'] == 'Safari on iPhone'
    row = [s for s in (await sign_ins.list_sign_ins(laptop))['sign_ins'] if s['id'] == phone.sid][0]
    assert row['method'] == 'code' and row['approved_by'] == 'Chrome on Windows'

    with pytest.raises(HTTPException) as exc_info:  # works once
        await sign_ins.code_sign_in(sign_ins.CodeSignIn(code=code), _request())
    assert exc_info.value.status_code == 400

    # The new device can't make codes for its first day.
    with pytest.raises(HTTPException) as exc_info:
        await sign_ins.make_sign_in_code(_request(), phone)
    assert exc_info.value.status_code == 403

    # Signing the new device out from the laptop ends it.
    await sign_ins.end_sign_in(phone.sid, laptop)
    with pytest.raises(HTTPException):
        await _ctx(out['access_token'])


async def test_codes_expire_cancel_and_cap(world):
    from cognitrix.api.routes import sign_ins
    from cognitrix.models.sign_in import SignInCode

    ctx = await _ctx(await _login())
    codes = [(await sign_ins.make_sign_in_code(_request(), ctx))['code'] for _ in range(4)]
    assert (await sign_ins.sign_in_code_status(codes[0], ctx))['status'] == 'expired'  # only 3 open at once
    await sign_ins.cancel_sign_in_code(codes[1], ctx)
    with pytest.raises(HTTPException):
        await sign_ins.code_sign_in(sign_ins.CodeSignIn(code=codes[1]), _request())
    await SignInCode.update_many({'user_id': str(world.ada.id)},
                                {'expires_at': int(time.time()) - 1})
    for code in codes[2:]:
        with pytest.raises(HTTPException):
            await sign_ins.code_sign_in(sign_ins.CodeSignIn(code=code), _request(ip='10.0.0.9'))


async def test_wrong_codes_are_limited_per_address(world):
    from cognitrix.api.routes import sign_ins

    for _ in range(10):
        with pytest.raises(HTTPException) as exc_info:
            await sign_ins.code_sign_in(sign_ins.CodeSignIn(code='AAAA-BBBB'), _request(ip='6.6.6.6'))
        assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        await sign_ins.code_sign_in(sign_ins.CodeSignIn(code='AAAA-BBBB'), _request(ip='6.6.6.6'))
    assert exc_info.value.status_code == 429


async def test_code_sign_in_can_be_turned_off(world, monkeypatch):
    from cognitrix.api.routes import auth, sign_ins

    monkeypatch.setenv('COGNITRIX_CODE_SIGNIN', 'false')
    assert (await auth.auth_options())['code_sign_in'] is False
    with pytest.raises(HTTPException) as exc_info:
        await sign_ins.make_sign_in_code(_request(), await _ctx(await _login()))
    assert exc_info.value.status_code == 403


async def test_password_change_and_sign_out_everywhere_end_other_devices(world):
    from cognitrix.api.routes import auth

    here, there = await _login(), await _login(SAFARI_IPHONE)
    ctx = await _ctx(here)
    fresh = (await auth.change_password(
        auth.PasswordChange(current_password='old-password', new_password='new-password-1'), ctx))['access_token']
    assert (await _ctx(fresh)).sid == ctx.sid
    with pytest.raises(HTTPException):
        await _ctx(there)
    await auth.logout(await _ctx(fresh))
    with pytest.raises(HTTPException):
        await _ctx(fresh)
