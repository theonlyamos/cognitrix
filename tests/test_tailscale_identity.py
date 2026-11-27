"""Who a visitor from the tailnet is, and the two things that use it: Apps
shared with the tailnet (opened with no Cognitrix account) and signing in with
a linked Tailscale login. Tailscale itself is never run: requests are sent
through the tailnet door with the headers Tailscale Serve adds."""
from types import SimpleNamespace

import httpx
import pytest

from cognitrix import app_settings
from cognitrix.tailscale import doors, identity
from tests.test_hosted_apps import _user, db, make_app  # noqa: F401 - db is a fixture

NAME = 'studio.tail1234.ts.net'
ADA = {'Tailscale-User-Login': 'Ada@Example.com', 'Tailscale-User-Name': '=?utf-8?q?Ada_Lovelac=C3=A9?=',
       'Tailscale-User-Profile-Pic': 'https://example.com/ada.png'}
CHROME = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'


def _on(signin=False):
    app_settings.set_many({'COGNITRIX_TAILSCALE': True, 'COGNITRIX_TAILSCALE_SERVE': True,
                           'COGNITRIX_TAILSCALE_SIGNIN': signin})


def _request(headers=None, door=doors.TAILNET):
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    headers.setdefault('user-agent', CHROME)
    return SimpleNamespace(headers=headers, scope={doors.SCOPE_KEY: door} if door else {},
                           client=SimpleNamespace(host='100.64.0.9'), query_params={}, base_url=f'https://{NAME}/')


def _client(door=doors.TAILNET):
    """The real proxy and Tailscale routes, reached the way a door reaches them."""
    from fastapi import FastAPI

    from cognitrix.api.routes.hosted_apps import app_proxy
    from cognitrix.api.routes.tailscale import tailscale_api

    api = FastAPI()
    api.include_router(app_proxy)
    api.include_router(tailscale_api, prefix='/api/v1')
    app = doors.DoorGuard(api)
    if door:
        app = doors.door_app(app, door)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:41001')


# ---------------------------------------------------------------------------
# Who is asking
# ---------------------------------------------------------------------------

def test_a_visitor_is_only_known_at_the_tailnet_door():
    seen = identity.visitor(_request(ADA))
    assert seen == identity.Visitor('ada@example.com', 'Ada Lovelacé', 'https://example.com/ada.png')
    assert seen.key == 'ts:ada@example.com' and identity.login_of(seen.key) == 'ada@example.com'
    # The same headers anywhere else mean nothing.
    assert identity.visitor(_request(ADA, door=doors.PUBLIC)) is None
    assert identity.visitor(_request(ADA, door=doors.DIRECT)) is None
    assert identity.visitor(_request(ADA, door='')) is None
    assert identity.visitor(None) is None
    # A tagged device has no login: nobody.
    assert identity.visitor(_request({'Tailscale-User-Name': 'ci'})) is None


@pytest.mark.parametrize('login', ['', ' ', 'a b@example.com', 'x' * 300, 'a\n@b', '<a>@b', 'a"@b', None])
def test_odd_logins_are_nobody(login):
    assert identity.clean_login(login) == ''
    assert identity.visitor(_request({'Tailscale-User-Login': login or ''})) is None


def test_pictures_are_https_only_and_names_are_trimmed():
    seen = identity.visitor(_request({'Tailscale-User-Login': 'bob@github', 'Tailscale-User-Name': 'B' * 500,
                                      'Tailscale-User-Profile-Pic': 'javascript:alert(1)'}))
    assert seen.login == 'bob@github' and seen.picture == '' and len(seen.name) == 120


@pytest.mark.parametrize('headers, another', [
    ({}, False),                                                            # not a browser (a PC's connector)
    ({'sec-fetch-site': 'same-origin', 'origin': 'https://elsewhere.example'}, False),   # the browser's word wins
    ({'sec-fetch-site': 'none'}, False),                                    # typed into the address bar
    ({'sec-fetch-site': 'cross-site'}, True),
    ({'sec-fetch-site': 'same-site'}, True),                                # another program on the same machine name
    ({'origin': f'https://{NAME}'}, False),                                 # an older browser, our own page
    ({'origin': f'https://{NAME}:8443', 'host': f'{NAME}:8443'}, False),
    ({'origin': f'https://{NAME}:10000', 'host': f'{NAME}:8443'}, True),
    ({'origin': 'https://evil.example'}, True),
    ({'origin': 'null'}, True),
])
def test_another_sites_page_is_told_apart(headers, another):
    headers = {'host': NAME, **headers}
    assert identity.from_another_site(SimpleNamespace(headers=headers)) is another
    request = _request({**ADA, **headers})
    assert (identity.visitor(request, own_page=True) is None) is another
    assert identity.visitor(request) is not None       # an App's own page still has its visitor


# ---------------------------------------------------------------------------
# Signing in from the tailnet
# ---------------------------------------------------------------------------

async def test_sign_in_is_off_until_an_admin_turns_it_on(db):  # noqa: F811
    ada = await _user('Ada', 'ada@example.com')
    _on(signin=False)
    assert (await identity.view(_request(ADA)))['enabled'] is False
    with pytest.raises(identity.IdentityError) as off:
        await identity.link(_request(ADA), ada)
    assert off.value.status == 403
    with pytest.raises(identity.IdentityError) as off:
        await identity.sign_in(_request(ADA))
    assert off.value.status == 403
    # On, but the private address is off: still nothing.
    app_settings.set_many({'COGNITRIX_TAILSCALE_SIGNIN': True, 'COGNITRIX_TAILSCALE_SERVE': False})
    assert identity.signin_on() is False


async def test_a_login_signs_in_only_after_its_owner_linked_it(db):  # noqa: F811
    from cognitrix.common.security import _resolve_jwt
    from cognitrix.models.sign_in import SignIn, TailscaleLink

    ada = await _user('Ada', 'ada@example.com')
    _on(signin=True)
    # The same email on both sides is not a link.
    seen = await identity.view(_request(ADA))
    assert seen == {'enabled': True, 'login': 'ada@example.com', 'name': 'Ada Lovelacé',
                    'picture': 'https://example.com/ada.png', 'can_sign_in': False}
    with pytest.raises(identity.IdentityError) as unknown:
        await identity.sign_in(_request(ADA))
    assert unknown.value.status == 401

    # Linking needs the request to come from the tailnet.
    with pytest.raises(identity.IdentityError) as away:
        await identity.link(_request(ADA, door=doors.DIRECT), ada)
    assert away.value.code == 'not_on_tailnet'
    mine = await identity.link(_request(ADA), ada)
    assert mine['linked'] == 'ada@example.com' and mine['can_sign_in'] is True

    user, token = await identity.sign_in(_request(ADA), name='', kind='')
    assert str(user.id) == str(ada.id)
    ctx = await _resolve_jwt(token)
    assert str(ctx.user.id) == str(ada.id)
    row = (await SignIn.find({'user_id': str(ada.id)}))[0]
    assert row.method == 'tailscale' and row.approved_by == 'ada@example.com' and row.address == '100.64.0.9'
    assert (await TailscaleLink.find_one({'login': 'ada@example.com'})).last_used_at

    # Nothing from the public address or the local network signs anyone in.
    for door in (doors.PUBLIC, doors.DIRECT):
        with pytest.raises(identity.IdentityError):
            await identity.sign_in(_request(ADA, door=door))

    # Unlinked: the login is nobody again.
    assert (await identity.unlink(_request(ADA), ada))['linked'] == ''
    with pytest.raises(identity.IdentityError):
        await identity.sign_in(_request(ADA))


async def test_one_login_one_account_and_disabled_accounts_stay_out(db):  # noqa: F811
    from cognitrix.models import User
    from cognitrix.models.sign_in import TailscaleLink

    ada = await _user('Ada', 'ada@example.com')
    bea = await _user('Bea', 'bea@example.com')
    _on(signin=True)
    await identity.link(_request(ADA), ada)
    with pytest.raises(identity.IdentityError) as taken:
        await identity.link(_request(ADA), bea)
    assert taken.value.status == 409
    # Linking again from another login moves the link: one login per account.
    await identity.link(_request({'Tailscale-User-Login': 'ada@github'}), ada)
    assert [row.login for row in await TailscaleLink.find({'user_id': str(ada.id)})] == ['ada@github']
    await identity.link(_request(ADA), bea)          # the old login is free again

    # If two rows ever named one login, it would sign nobody in rather than pick one.
    await TailscaleLink(user_id=str(ada.id), login='ada@example.com').save()
    assert await identity.link_of_login('ada@example.com') is None
    with pytest.raises(identity.IdentityError):
        await identity.sign_in(_request(ADA))
    await TailscaleLink.delete_many({'user_id': str(ada.id)})
    assert (await identity.sign_in(_request(ADA)))[0].email == 'bea@example.com'

    await User.update_one({'id': str(bea.id)}, {'disabled': True})
    with pytest.raises(identity.IdentityError) as out:
        await identity.sign_in(_request(ADA))
    assert out.value.status == 401


async def test_sign_in_routes_through_the_doors(db):  # noqa: F811
    ada = await _user('Ada', 'ada@example.com')
    _on(signin=True)
    await identity.link(_request(ADA), ada)
    # The sign-in page is told there is an account to continue as, only on the tailnet.
    from cognitrix.api.routes import auth

    assert (await auth.auth_options(request=_request(ADA)))['tailscale_login'] == 'ada@example.com'
    assert (await auth.auth_options(request=_request({'Tailscale-User-Login': 'eve@example.com'})))['tailscale_login'] == ''
    assert (await auth.auth_options(request=_request(ADA, door=doors.DIRECT)))['tailscale_login'] == ''
    assert (await auth.auth_options())['tailscale_login'] == ''
    async with _client() as tailnet, _client(doors.PUBLIC) as public, _client(None) as direct:
        me = (await tailnet.get('/api/v1/tailscale/me', headers=ADA)).json()
        assert me['login'] == 'ada@example.com' and me['can_sign_in'] is True
        done = await tailnet.post('/api/v1/tailscale/sign-in', json={}, headers=ADA)
        assert done.status_code == 200 and done.json()['access_token']
        # Another site's page, open in a browser on the tailnet, can't use who that browser is.
        for forged in ({'Origin': 'https://evil.example'}, {'Sec-Fetch-Site': 'cross-site'},
                       {'Origin': 'null', 'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'}):
            assert (await tailnet.get('/api/v1/tailscale/me', headers={**ADA, **forged})).json()['login'] == ''
            assert (await tailnet.post('/api/v1/tailscale/sign-in', json={}, headers={**ADA, **forged})).status_code == 401
        own = {**ADA, 'X-Forwarded-Host': NAME, 'Origin': f'https://{NAME}', 'Sec-Fetch-Site': 'same-origin'}
        assert (await tailnet.post('/api/v1/tailscale/sign-in', json={}, headers=own)).status_code == 200
        # Someone on the local network sending Tailscale's headers themselves.
        assert (await direct.get('/api/v1/tailscale/me', headers=ADA)).json()['login'] == ''
        assert (await direct.post('/api/v1/tailscale/sign-in', json={}, headers=ADA)).status_code == 401
        # The public address doesn't even have these routes.
        assert (await public.post('/api/v1/tailscale/sign-in', json={}, headers=ADA)).status_code == 404


# ---------------------------------------------------------------------------
# Apps shared with the tailnet
# ---------------------------------------------------------------------------

async def test_sharing_an_app_with_tailscale_logins(db, tmp_path):  # noqa: F811
    from cognitrix.hosted_apps import store

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Board', folder=str(tmp_path / 'b'),
                                    command=make_app(tmp_path / 'b', 'hi'))
    _on()
    assert store.role_of(app, 'ts:ada@example.com') is None
    app = await store.set_sharing(app, tailnet=[' Ada@Example.com ', 'bob@github'])
    assert store.tailnet_shares(app) == {'everyone': False, 'logins': ['ada@example.com', 'bob@github']}
    assert store.access_label(app) == 'tailnet'
    assert store.role_of(app, 'ts:ada@example.com') == 'view'
    assert store.role_of(app, 'ts:eve@example.com') is None
    # A Tailscale login is not an account: sharing by login gives the account nothing.
    assert store.role_of(app, str(bea.id)) is None and store.role_of(app, 'ada@example.com') is None

    # Changing the people leaves the tailnet list alone, and can't write into it.
    app = await store.set_sharing(app, people={str(bea.id): 'edit', 'ts:eve@example.com': 'edit', '@tailnet': 'edit'})
    assert store.role_of(app, str(bea.id)) == 'edit' and store.role_of(app, 'ts:eve@example.com') is None
    assert store.tailnet_shares(app)['logins'] == ['ada@example.com', 'bob@github']
    seen = store.project(app, 'owner', people={}, agents={})
    assert seen['shared_with'] == {str(bea.id): 'edit'} and seen['tailnet']['logins'] == ['ada@example.com', 'bob@github']
    assert seen['access'] == 'people'
    assert store.project(app, 'edit', people={}, agents={})['tailnet'] == {'everyone': False, 'logins': []}

    # Everyone on the tailnet; "everyone on this server" never means them.
    app = await store.set_sharing(app, people={}, tailnet=['*'])
    assert store.role_of(app, 'ts:eve@example.com') == 'view' and store.access_label(app) == 'tailnet'
    app = await store.set_sharing(app, tailnet=[], everyone='edit')
    assert store.role_of(app, 'ts:eve@example.com') is None and store.role_of(app, str(bea.id)) == 'edit'

    with pytest.raises(store.AppError):
        await store.set_sharing(app, tailnet=['not a login'])
    with pytest.raises(store.AppError):
        await store.set_sharing(app, tailnet=[f'p{i}@example.com' for i in range(store.MAX_TAILNET_LOGINS + 1)])

    # With the private address off there are no visitors from the tailnet.
    app = await store.set_sharing(app, tailnet=['*'])
    app_settings.set_many({'COGNITRIX_TAILSCALE_SERVE': False})
    assert store.role_of(app, 'ts:ada@example.com') is None


async def test_a_tailnet_visitor_opens_an_app_with_no_account(db, tmp_path):  # noqa: F811
    from cognitrix.hosted_apps import store

    amos = await _user('Amos', 'amos@example.com')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Board', folder=str(tmp_path / 'b'),
                                    command=make_app(tmp_path / 'b', 'hi'))
    eve = {'Tailscale-User-Login': 'eve@example.com', 'Tailscale-User-Name': 'Eve'}
    _on()
    async with _client() as tailnet, _client(None) as direct, _client(doors.PUBLIC) as public:
        # Not shared with the tailnet yet: there is no such app, as far as a visitor can tell.
        assert (await tailnet.get('/api/v1/tailscale/apps/board', headers=ADA)).status_code == 404
        assert (await tailnet.get('/api/v1/tailscale/apps/nothing', headers=ADA)).status_code == 404
        await store.set_sharing(app, tailnet=['ada@example.com'])
        assert (await tailnet.get('/api/v1/tailscale/apps/board', headers=eve)).status_code == 404
        assert (await tailnet.get('/api/v1/tailscale/apps/board')).status_code == 404           # a tagged device
        assert (await direct.get('/api/v1/tailscale/apps/board', headers=ADA)).status_code == 404
        assert (await public.get('/api/v1/tailscale/apps/board', headers=ADA)).status_code == 404

        other_site = {**ADA, 'Origin': 'https://evil.example', 'Sec-Fetch-Site': 'cross-site'}
        assert (await tailnet.get('/api/v1/tailscale/apps/board', headers=other_site)).status_code == 404
        way_in = (await tailnet.get('/api/v1/tailscale/apps/board', headers=ADA)).json()
        assert way_in['title'] == 'Board' and way_in['login'] == 'ada@example.com'
        url = way_in['url']
        # A page load stays where it is (there is no Cognitrix page for someone with no account).
        page = await tailnet.get(url + 'items?q=1', headers={**ADA, 'Sec-Fetch-Dest': 'document',
                                                              'X-Cognitrix-User-Role': 'owner',
                                                              'X-Cognitrix-User-Name': 'Amos'})
        assert page.status_code == 200, page.text
        assert page.json()['user'] == 'Ada Lovelac%C3%A9' and page.json()['path'] == '/items?q=1'
        assert 'sandbox allow-scripts' in page.headers['content-security-policy']

        # The link is only good while Tailscale says it is them: not for someone else on the
        # tailnet, not from the local network, not once the owner stops sharing.
        for client, headers in ((tailnet, eve), (tailnet, {}), (direct, ADA)):
            r = await client.get(url, headers=headers)
            assert r.status_code == 302 and r.headers['location'] == '/apps/open/board'
        assert (await public.get(url, headers=ADA)).status_code == 404
        await store.set_sharing(app, tailnet=[])
        assert (await tailnet.get(url, headers=ADA)).status_code == 403


async def test_the_app_is_told_a_guest_from_an_account(db, tmp_path, monkeypatch):  # noqa: F811
    from cognitrix.hosted_apps import bridge, store

    amos = await _user('Amos', 'amos@example.com')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Board', folder=str(tmp_path / 'b'),
                                    command=make_app(tmp_path / 'b', 'hi'))
    _on()
    await store.set_sharing(app, tailnet=['*'])
    sent: list[dict] = []
    real = httpx.AsyncClient.build_request

    def spy(self, method, url, **kwargs):
        sent.append({k.lower(): v for k, v in dict(kwargs.get('headers') or {}).items()})
        return real(self, method, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, 'build_request', spy)
    forged = {'X-Cognitrix-User-Id': 'someone-else', 'X-Cognitrix-User-Source': 'forged', 'X-Cognitrix-User-Extra': '1'}
    async with _client() as tailnet:
        url = (await tailnet.get('/api/v1/tailscale/apps/board', headers=ADA)).json()['url']
        sent.clear()
        assert (await tailnet.get(url, headers={**ADA, **forged})).status_code == 200
        guest = next(h for h in sent if 'x-cognitrix-user-role' in h)
        assert guest['x-cognitrix-user-id'] == 'ada@example.com' and guest['x-cognitrix-user-email'] == 'ada@example.com'
        assert guest['x-cognitrix-user-role'] == 'view' and guest['x-cognitrix-user-source'] == 'tailscale'
        assert 'x-cognitrix-user-extra' not in guest and not [h for h in guest if h.startswith('tailscale-')]

        sent.clear()
        token = store.open_token('board', str(amos.id))
        assert (await tailnet.get(f'/a/board/~{token}/', headers={**ADA, **forged})).status_code == 200
        owner = next(h for h in sent if 'x-cognitrix-user-role' in h)
        assert owner['x-cognitrix-user-id'] == str(amos.id) and owner['x-cognitrix-user-role'] == 'owner'
        assert 'x-cognitrix-user-source' not in owner and 'x-cognitrix-user-extra' not in owner

    # A guest's page knows it has nobody to ask.
    page = b'<html><head></head><body></body></html>'
    assert b'__cognitrixGuest=1' in bridge.inject(page, guest=True)
    assert b'__cognitrixGuest=1' not in bridge.inject(page)
    assert bridge.inject(bridge.inject(page, guest=True), guest=True).count(b'__cognitrixGuest=1') == 1
