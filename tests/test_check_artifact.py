"""Check Artifact: agents look at their pages in light and dark, on phone and desktop.

- Views are checked and defaulted to all four.
- The tool reports each view, flags sideways overflow and attaches screenshots.
- With a real browser installed, dark mode really is dark and errors are caught.
- Without a browser here, the agents' computer does the looking; with neither, a clear error.
"""
import base64
import struct
import zlib

import pytest

from tests.test_artifact_pages import _as, _page_id, _tool, _user, db  # noqa: F401


def test_views_default_to_all_four_and_bad_ones_are_refused():
    from cognitrix.artifact_pages import check
    from cognitrix.artifact_pages.store import PageError

    assert check.clean_views(None) == ['desktop-light', 'desktop-dark', 'phone-light', 'phone-dark']
    assert check.clean_views(['Phone-Dark', 'phone-dark', 'desktop-light']) == ['phone-dark', 'desktop-light']
    assert check.clean_views('desktop-dark, phone-light') == ['desktop-dark', 'phone-light']
    with pytest.raises(PageError):
        check.clean_views(['tablet'])


async def test_the_tool_reports_every_view_and_attaches_the_shots(db, monkeypatch):  # noqa: F811
    from cognitrix.artifact_pages import check
    from cognitrix.tools import misc

    seen = {}

    async def fake_look(page, source, owner, *, views, full_page, scroll_y):
        seen.update(source=source, views=views, full_page=full_page, scroll_y=scroll_y)
        return [
            {'view': 'desktop-dark', 'ok': True, 'width': 1280, 'height': 800, 'page_height': 3000,
             'page_width': 1280, 'shot_top': 0, 'shot_height': 2400, 'text_length': 40, 'screenshot': 'AAA',
             'errors': ['TypeError: x is undefined'], 'console': [], 'failed': []},
            {'view': 'phone-dark', 'ok': True, 'width': 390, 'height': 844, 'page_height': 900,
             'page_width': 620, 'shot_top': 0, 'shot_height': 900, 'text_length': 40, 'screenshot': 'BBB',
             'errors': [], 'console': [], 'failed': []},
        ]

    monkeypatch.setattr(check, 'look', fake_look)
    images: list[str] = []
    token = misc._round_images.set(images)
    try:
        amos = await _user('Amos', 'amos@example.com')
        _as(amos)
        pid = _page_id(await _tool('publish_artifact', title='Inbox drafts', content='<p>A</p>'))
        out = await _tool('check_artifact', artifact_id=pid, views=['desktop-dark', 'phone-dark'])
    finally:
        misc._round_images.reset(token)

    assert out.status == 'success'
    assert seen == {'source': '<p>A</p>', 'views': ['desktop-dark', 'phone-dark'], 'full_page': True, 'scroll_y': 0}
    assert 'check again with scroll_y=2400' in out.text
    assert 'TypeError: x is undefined' in out.text
    assert '620 px wide, wider than the screen' in out.text
    assert images == ['data:image/png;base64,AAA', 'data:image/png;base64,BBB']

    bad = await _tool('check_artifact', artifact_id=pid, views=['tablet'])
    assert bad.error.code == 'bad_view'


async def test_someone_elses_artifact_cannot_be_checked(db):  # noqa: F811
    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    _as(amos)
    pid = _page_id(await _tool('publish_artifact', title='Private', content='<p>A</p>'))
    _as(kofi)
    assert (await _tool('check_artifact', artifact_id=pid)).status != 'success'


async def test_no_browser_and_no_sandbox_says_so(monkeypatch):
    from types import SimpleNamespace

    from cognitrix.artifact_pages import check
    from cognitrix.artifact_pages.store import PageError
    from cognitrix.computer.sandbox import client, pagecheck

    monkeypatch.setattr(pagecheck, 'find_browser', lambda: None)
    monkeypatch.setattr(client, 'sandbox_mode', lambda: None)
    with pytest.raises(PageError) as err:
        await check.look(SimpleNamespace(format='html', title='T'), '<p>x</p>', 'u1', views=['desktop-light'])
    assert err.value.code == 'no_browser'


async def test_without_a_browser_the_agents_computer_looks(monkeypatch):
    from types import SimpleNamespace

    from cognitrix.artifact_pages import check
    from cognitrix.computer.sandbox import client, pagecheck

    sent = {}

    class Box:
        def request(self, method, path, body=None, timeout=None):
            sent.update(method=method, path=path, body=body)
            return {'ok': True, 'views': [{'view': 'phone-dark', 'ok': True}]}

    monkeypatch.setattr(pagecheck, 'find_browser', lambda: None)
    monkeypatch.setattr(client, 'sandbox_mode', lambda: 'docker')
    monkeypatch.setattr(client, 'get_manager', lambda: SimpleNamespace(sandbox=lambda owner: Box()))
    out = await check.look(SimpleNamespace(format='markdown', title='Notes'), '# Hi', 'u1', views=['phone-dark'])
    assert out == [{'view': 'phone-dark', 'ok': True}]
    assert sent['path'] == '/pagecheck' and sent['body']['views'] == ['phone-dark']
    assert '<!doctype html>' in sent['body']['html']  # rendered the way the viewer serves it
    assert "default-src 'none'" in sent['body']['csp'] and 'sandbox' in sent['body']['csp']


def _pixel(png_b64: str, x: int, y: int) -> tuple[int, int, int]:
    """One RGB pixel of a PNG Chromium wrote (8-bit RGB or RGBA, no interlace)."""
    data = base64.b64decode(png_b64)
    pos, chunks, width, kind = 8, [], 0, 6
    while pos < len(data):
        length, tag = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if tag == b'IHDR':
            width, _, _, kind = struct.unpack('>IIBB', body[:10])
        elif tag == b'IDAT':
            chunks.append(body)
        pos += 12 + length
    raw = zlib.decompress(b''.join(chunks))
    bpp = 4 if kind == 6 else 3
    stride = width * bpp
    rows, prev = [], bytearray(stride)
    for r in range(y + 1):
        start = r * (stride + 1)
        f, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    px = rows[y][x * bpp:x * bpp + 3]
    return px[0], px[1], px[2]


async def test_a_real_browser_sees_dark_mode_and_errors():
    from types import SimpleNamespace

    from cognitrix.artifact_pages import check
    from cognitrix.computer.sandbox import pagecheck

    if pagecheck.find_browser() is None:
        pytest.skip('no Chromium-family browser installed')
    html = ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<style>body{margin:0;background:#fff}@media (prefers-color-scheme:dark){body{background:#000}}'
            '</style></head><body><p>Draft A</p><div style="height:3000px"></div>'
            '<img src="http://127.0.0.1:9/x.png">'
            '<script>console.error("boom")</script></body></html>')
    out = await check.look(SimpleNamespace(format='html', title='T'), html, 'u1',
                           views=['desktop-light', 'phone-dark'])
    light, dark = out
    assert light['ok'] and dark['ok']
    assert light['width'] == 1280 and dark['width'] == 390 and dark['page_width'] == 390
    assert light['page_height'] > 3000 and light['shot_height'] == pagecheck.MAX_SHOT_HEIGHT
    assert 'boom' in light['console']
    # Served with the viewer's policy: what the viewer blocks is blocked (and reported) here too.
    assert any('Content Security Policy' in line for line in light['console'] + light['failed'])
    assert _pixel(light['screenshot'], 5, 200) == (255, 255, 255)
    assert _pixel(dark['screenshot'], 5, 200) == (0, 0, 0)
