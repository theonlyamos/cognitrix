"""Every app has an icon (cognitrix/hosted_apps/icons.py).

- Publish App picks up icon.svg / icon.png from the app's folder; without one the
  app shows its initials and the agent is told to make a real icon.
- An icon a person uploads survives later publishes; resetting goes back to initials.
- Only real images are accepted; SVGs with scripts are refused, and icons are
  served with a CSP that keeps an SVG from running anything.
- Apps made before icons pick one up from their folder at startup.
- Generate Image can save an icon into an app's folder, and nowhere else.
"""
import pytest

from tests.test_hosted_apps import _as, _proxy_client, _tool, _user, db, make_app  # noqa: F401

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
       b'<rect width="256" height="256" rx="56" fill="#7dd3fc"/></svg>')
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


def test_kinds_and_unsafe_svgs():
    from cognitrix.hosted_apps import icons

    assert icons.kind_of(SVG) == 'svg'
    assert icons.kind_of(b'<?xml version="1.0"?>\n' + SVG) == 'svg'
    assert icons.kind_of(PNG) == 'png'
    assert icons.kind_of(b'\xff\xd8\xff\xe0' + b'0' * 20) == 'jpg'
    for bad in (b'', b'GIF89a....', b'<html><body>hi</body></html>', b'x' * (icons.MAX_ICON_BYTES + 1)):
        with pytest.raises(icons.IconError):
            icons.kind_of(bad)
    for evil in (b'<svg><script>alert(1)</script></svg>', b'<svg onload="alert(1)"></svg>',
                 b'<svg><a href="javascript:alert(1)">x</a></svg>',
                 b'<svg><foreignObject><iframe/></foreignObject></svg>'):
        with pytest.raises(icons.IconError):
            icons.kind_of(evil)


def test_generated_icon_and_folder_lookup(tmp_path):
    from cognitrix.hosted_apps import icons

    svg = icons.generated_svg('team dashboard', 'team-dashboard').decode()
    assert '>TD</text>' in svg and icons.color('team-dashboard') in svg
    assert '>T&lt;</text>' in icons.generated_svg('team <script>', 'x').decode()  # escaped
    assert icons.letters('') == '?' and icons.letters('todo') == 'T'
    assert icons.stamp('Team Dash') == icons.stamp('Tiny Dots') != icons.stamp('Other')

    folder = tmp_path / 'app'
    (folder / 'public').mkdir(parents=True)
    assert icons.find_in_folder(folder) is None
    (folder / 'public' / 'favicon.png').write_bytes(PNG)
    assert icons.find_in_folder(folder).name == 'favicon.png'
    (folder / 'icon.svg').write_bytes(SVG)
    assert icons.find_in_folder(folder).name == 'icon.svg'  # icon.svg comes first
    (tmp_path / 'outside.png').write_bytes(PNG)
    assert icons.find_in_folder(folder, '../outside.png') is None
    assert icons.find_in_folder(folder, 'public/favicon.png').name == 'favicon.png'


async def test_publish_picks_up_the_icon_or_asks_for_one(db, tmp_path):  # noqa: F811 (the db fixture)
    from cognitrix.hosted_apps import icons, store

    amos = await _user('Amos', 'amos@example.com')
    _as(amos, session_id='s1', agent_id='a1')
    build = store.apps_root() / 'todo-list'
    cmd = make_app(build)
    out = await _tool('publish_app', title='Todo list', start_command=cmd)
    assert out.status == 'success', out.text
    assert 'no icon yet' in out.text and 'icon.svg' in out.text
    app = await store.get_app(out.entities[0].id)
    assert app.icon == icons.GENERATED and icons.icon_file(str(app.id)) is None
    assert store.project(app, store.ROLE_OWNER, people={}, agents={})['icon'] == 'generated'

    (build / 'icon.svg').write_bytes(SVG)
    again = await _tool('publish_app', title='Todo list', start_command=cmd, app_id=str(app.id))
    assert again.status == 'success' and 'no icon yet' not in again.text
    app = await store.get_app(app.id)
    path, media = icons.icon_file(str(app.id))
    assert app.icon == icons.CUSTOM and media == 'image/svg+xml' and path.read_bytes() == SVG

    # A broken icon file is reported; the earlier icon stays.
    (build / 'icon.svg').write_bytes(b'<svg onload="x()"></svg>')
    third = await _tool('publish_app', title='Todo list', start_command=cmd, app_id=str(app.id))
    assert 'icon.svg was not used' in third.text
    assert icons.icon_file(str(app.id))[0].read_bytes() == SVG

    # Deleting the app removes its icon.
    await store.delete_app(await store.get_app(app.id))
    assert icons.icon_file(str(app.id)) is None


async def test_uploaded_icon_survives_publishes_and_is_served_safely(db, tmp_path):  # noqa: F811 (the db fixture)
    from cognitrix.hosted_apps import icons, store

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Board', folder=str(tmp_path / 'b'),
                                    command=make_app(tmp_path / 'b'))
    assert app.icon == icons.GENERATED
    with pytest.raises(store.AppError):
        await store.set_icon(app, b'not an image')
    await store.set_icon(app, PNG)
    app, _, _ = await store.publish(user_key=str(amos.id), title='Board', folder=app.folder, command=app.command,
                                    app_id=str(app.id))
    assert app.icon == icons.CUSTOM and icons.icon_file(str(app.id))[0].read_bytes() == PNG

    async with _proxy_client() as client:
        token = store.open_token('board', str(amos.id), thumb=True)
        r = await client.get(f'/app-icons/board?t={token}')
        assert r.status_code == 200 and r.content == PNG and r.headers['content-type'] == 'image/png'
        assert 'sandbox' in r.headers['content-security-policy'] and r.headers['x-content-type-options'] == 'nosniff'
        # Not shared with Bea, and a bad token: nothing.
        assert (await client.get(f'/app-icons/board?t={store.open_token("board", str(bea.id), thumb=True)}')).status_code == 404
        assert (await client.get('/app-icons/board?t=nope')).status_code == 404

        await store.set_icon(app, None)
        r = await client.get(f'/app-icons/board?t={token}')
        assert r.status_code == 200 and r.headers['content-type'].startswith('image/svg+xml') and b'>B</text>' in r.content
    assert app.icon == icons.GENERATED and icons.icon_file(str(app.id)) is None


async def test_older_apps_pick_up_an_icon_at_startup(db, tmp_path):  # noqa: F811 (the db fixture)
    from cognitrix.hosted_apps import icons, store
    from cognitrix.hosted_apps.models import HostedApp

    with_icon = store.apps_root() / 'has-icon'
    with_icon.mkdir()
    (with_icon / 'favicon.svg').write_bytes(SVG)
    await HostedApp(owner_id='u1', slug='has-icon', title='Has icon', runtime='local', folder=str(with_icon)).save()
    await HostedApp(owner_id='u1', slug='plain', title='Plain', runtime='local', folder=str(tmp_path / 'gone')).save()
    assert await store.backfill_icons() == 2
    one, two = await store.by_slug('has-icon'), await store.by_slug('plain')
    assert one.icon == icons.CUSTOM and icons.icon_file(str(one.id)) is not None
    assert two.icon == icons.GENERATED
    assert await store.backfill_icons() == 0


async def test_generate_image_saves_only_into_an_app_folder(db, tmp_path, monkeypatch):  # noqa: F811 (the db fixture)
    from cognitrix.hosted_apps import store
    from cognitrix.tools import image

    amos = await _user('Amos', 'amos@example.com')
    _as(amos)
    assert isinstance(image._save_target('../evil.png'), image.ToolOutcome)
    assert isinstance(image._save_target(str(store.apps_root() / 'icon.png')), image.ToolOutcome)  # not in an app
    assert isinstance(image._save_target(str(store.apps_root() / 'todo' / 'icon.exe')), image.ToolOutcome)
    assert isinstance(image._save_target(str(tmp_path / 'elsewhere' / 'icon.png')), image.ToolOutcome)
    good = image._save_target('todo/icon.png')
    assert good == (store.apps_root() / 'todo' / 'icon.png').resolve()
