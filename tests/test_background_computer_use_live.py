"""Live background computer use on a real Windows desktop.

Opens Notepad, Calculator and File Explorer windows, drives them through the
driver and checks the effects on disk and in the apps. Opt in with
COGNITRIX_LIVE_DESKTOP=1 (it pops up windows); skipped everywhere else.
"""
import os
import re
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != 'win32' or os.environ.get('COGNITRIX_LIVE_DESKTOP') != '1',
    reason='live desktop test: Windows with COGNITRIX_LIVE_DESKTOP=1 only',
)


@pytest.fixture(scope='module')
def driver():
    from cognitrix.computer import Driver
    from cognitrix.computer.windows import WindowsBackend, user32
    if not user32.GetForegroundWindow():
        pytest.skip('no interactive desktop')
    return Driver(WindowsBackend())


@pytest.fixture
def opened(driver):
    """Windows a test opened; closed afterwards (never anyone else's)."""
    from cognitrix.computer.windows import user32
    ids: list[int] = []
    yield ids
    for wid in ids:
        for _ in range(3):
            user32.PostMessageW(wid, 0x0010, 0, 0)  # WM_CLOSE
            for _ in range(20):
                if not user32.IsWindow(wid):
                    break
                time.sleep(0.25)
            if not user32.IsWindow(wid):
                break
    assert not any(user32.IsWindow(w) for w in ids), 'a test window did not close'


def _index(snap, role, name, start=0):
    for i, e in enumerate(snap.elements[start:], start):
        if e.role == role and e.name == name:
            return i
    # Don't list the other controls: they can include the person's own content.
    raise AssertionError(f'{role} {name!r} not found among {len(snap.elements)} controls')


def _assert_ours_not_in_front(driver, ids):
    # Only meaningful when someone's window was in front to begin with: with no
    # foreground window at all, Windows lets a newly started app take it.
    if driver.start_fg and driver.start_fg not in ids:
        assert driver.backend.foreground() not in ids, 'a test window took the foreground'


@pytest.fixture(autouse=True)
def _start_fg(driver):
    from cognitrix.computer.windows import user32
    time.sleep(1)  # a window closed by the previous test may still be "in front"
    fg = driver.backend.foreground()
    driver.start_fg = fg if fg and user32.IsWindow(fg) and driver.backend.window(fg) else 0


def _skip_if_running(driver, app):
    if any(w.app == app for w in driver.list_windows()):
        pytest.skip(f'{app} is already open; the test only uses windows it opens itself')


def test_calculator_by_elements_and_pixels(driver, opened):
    _skip_if_running(driver, 'calculatorapp.exe')
    win = driver.open('Calculator')
    assert win is not None, 'Calculator window did not appear'
    opened.append(win.id)
    _assert_ours_not_in_front(driver, opened)
    image, snap, _, _ = driver.look(win.id)
    assert image is not None
    for name in ('One', 'Two', 'Multiply by', 'Three', 'Four', 'Equals'):
        result = driver.act_on_element(win.id, _index(snap, 'Button', name))
        assert result.status == 'confirmed', (name, result)
        _assert_ours_not_in_front(driver, opened)
    _, snap, shown, _ = driver.look(win.id, 'Display is')
    assert any(e.name == 'Display is 408' for _, e in shown), [e.name for _, e in shown]
    # A pixel click on "Five" lands on the button through the element under the point.
    five = snap.elements[_index(snap, 'Button', 'Five')]
    x = ((five.rect[0] + five.rect[2]) / 2 - snap.origin[0]) / snap.scale
    y = ((five.rect[1] + five.rect[3]) / 2 - snap.origin[1]) / snap.scale
    assert driver.click_point(win.id, x, y).status == 'confirmed'
    _, _, shown, _ = driver.look(win.id, 'Display is')
    assert any(e.name == 'Display is 5' for _, e in shown)
    _assert_ours_not_in_front(driver, opened)


def _drop_my_tab(driver, path):
    """Remove the test's own tab from Notepad even after a failure, so Notepad's
    restored session keeps only the person's tabs: reopening the file switches
    to its tab, then save and Ctrl+W."""
    showing = [w for w in driver.list_windows() if path.name.lower() in w.title.lower()]
    win = showing[0] if showing else driver.open(str(path))
    if win is None or path.name.lower() not in win.title.lower():
        return
    if win.title.startswith('*'):
        driver.press_key(win.id, 'ctrl+s')
        time.sleep(0.5)
    if driver.press_key(win.id, 'ctrl+w').status == 'needs_screen':
        # Shortcuts aren't sent to the app in front; use its menu instead.
        _menu(driver, win.id, 'Close tab')


def _menu(driver, window_id, item):
    """Open Notepad's File menu and pick an item (the menu can take a moment)."""
    for _ in range(3):
        _, snap, _, _ = driver.look(window_id)
        driver.act_on_element(window_id, _index(snap, 'MenuItem', 'File'), 'expand')
        time.sleep(0.5)
        _, snap, _, _ = driver.look(window_id)
        if any(e.role == 'MenuItem' and e.name == item for e in snap.elements):
            return driver.act_on_element(window_id, _index(snap, 'MenuItem', item))
    raise AssertionError(f'menu item {item!r} did not appear')


def test_notepad_set_text_and_save(driver, opened, tmp_path):
    _skip_if_running(driver, 'notepad.exe')
    path = tmp_path / 'cognitrix-live.txt'
    path.write_text('', encoding='utf-8')
    win = driver.open(str(path))
    assert win is not None and 'cognitrix-live' in win.title
    opened.append(win.id)
    try:
        _, snap, _, _ = driver.look(win.id)
        editor = _index(snap, 'Document', 'Text editor')
        sentence = 'Written in the background by Cognitrix.'
        assert driver.set_text(win.id, sentence, editor).status == 'confirmed'
        _assert_ours_not_in_front(driver, opened)
        # Ctrl+S goes to Notepad's own input thread; the person's keyboard isn't
        # involved. A value set through UI Automation doesn't mark the tab as
        # modified, so the save can't be seen in the window: check the disk.
        result = driver.press_key(win.id, 'ctrl+s')
        if result.status == 'needs_screen' and not driver.start_fg:
            # Nothing else was in front, so Notepad legitimately is, and the
            # driver won't send shortcuts to the foreground app: use the menu.
            result = _menu(driver, win.id, 'Save')
        assert result.status in ('confirmed', 'unverified') and not result.focus_changed, result
        for _ in range(20):
            if path.read_text(encoding='utf-8-sig') == sentence:
                break
            time.sleep(0.25)
        assert path.read_text(encoding='utf-8-sig') == sentence
        _assert_ours_not_in_front(driver, opened)
    finally:
        _drop_my_tab(driver, path)


def test_notepad_typing_is_verified(driver, opened, tmp_path):
    _skip_if_running(driver, 'notepad.exe')
    path = tmp_path / 'cognitrix-typing.txt'
    path.write_text('', encoding='utf-8')
    win = driver.open(str(path))
    assert win is not None
    opened.append(win.id)
    try:
        _, snap, _, _ = driver.look(win.id)
        editor = snap.elements[_index(snap, 'Document', 'Text editor')]
        x = ((editor.rect[0] + editor.rect[2]) / 2 - snap.origin[0]) / snap.scale
        y = ((editor.rect[1] + editor.rect[3]) / 2 - snap.origin[1]) / snap.scale
        result = driver.type_text(win.id, 'typed in the background', x, y)
        assert result.status == 'confirmed', result
        _assert_ours_not_in_front(driver, opened)
        assert driver.backend.window(win.id).title.startswith('*')  # Notepad saw a real edit
        result = driver.press_key(win.id, 'ctrl+s')
        if result.status == 'needs_screen' and not driver.start_fg:
            result = _menu(driver, win.id, 'Save')  # Notepad is legitimately in front
        assert result.status == 'confirmed', result
        time.sleep(0.5)
        assert path.read_text(encoding='utf-8-sig') == 'typed in the background'
    finally:
        _drop_my_tab(driver, path)


def test_explorer_navigate_select_rename_and_minimize(driver, opened, tmp_path):
    (tmp_path / 'sub' / 'rename-me').mkdir(parents=True)
    win = driver.open(str(tmp_path))
    assert win is not None
    opened.append(win.id)
    _, snap, _, _ = driver.look(win.id)
    assert driver.act_on_element(win.id, _index(snap, 'ListItem', 'sub')).status == 'confirmed'
    for _ in range(10):
        if driver.backend.window(win.id).title.startswith('sub'):
            break
        time.sleep(0.3)
    _, snap, _, _ = driver.look(win.id)
    item = _index(snap, 'ListItem', 'rename-me')
    # "select" selects without opening the folder.
    result = driver.act_on_element(win.id, item, 'select')
    assert result.status == 'confirmed' and 'selected=True' in result.message
    assert driver.backend.window(win.id).title.startswith('sub')
    _, snap, _, _ = driver.look(win.id)
    name_box = _index(snap, 'Edit', 'Name', _index(snap, 'ListItem', 'rename-me'))
    assert driver.set_text(win.id, 'renamed-by-cognitrix', name_box).status in ('confirmed', 'unverified')
    for _ in range(10):
        if (tmp_path / 'sub' / 'renamed-by-cognitrix').exists():
            break
        time.sleep(0.3)
    assert sorted(p.name for p in (tmp_path / 'sub').iterdir()) == ['renamed-by-cognitrix']
    _assert_ours_not_in_front(driver, opened)
    # Minimize it, then look: restored behind other windows, without focus.
    _, snap, _, _ = driver.look(win.id, 'Minimize')
    driver.act_on_element(win.id, _index(snap, 'Button', 'Minimize'))
    time.sleep(0.5)
    assert driver.backend.window(win.id).minimized
    fg = driver.backend.foreground()
    image, _, _, note = driver.look(win.id)
    assert image is not None and 'restored' in note
    assert driver.backend.foreground() == fg


_BROWSERS = [
    r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe',
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
]


def _page_link(snap):
    for i, e in enumerate(snap.elements):
        slug = (e.value or '').split('/wiki/')[-1]
        if (e.role == 'Link' and '/wiki/' in (e.value or '') and ':' not in slug and '#' not in slug
                and len(e.name) > 3 and 'Main_Page' not in e.value and e.rect[1] > snap.origin[1] + 120
                and e.rect[3] < snap.origin[1] + snap.size[1] - 10):
            return i, e
    raise AssertionError('no article link on the page')


def test_browser_in_a_throwaway_profile(driver, opened, tmp_path):
    """A new Chromium window with its own profile: links and buttons by element
    and by pixel, address bar text; typing and keys need the screen."""
    import shutil
    browser = next((p for p in _BROWSERS if os.path.exists(p)), None)
    if browser is None:
        pytest.skip('no Chromium browser installed')
    profile = tmp_path / 'profile'
    win = driver.open(browser, timeout_s=25, args=(
        f'--user-data-dir="{profile}" --no-first-run --no-default-browser-check '
        '--new-window https://en.wikipedia.org/wiki/Main_Page'))
    assert win is not None and win.kind == 'chromium'
    opened.append(win.id)
    _assert_ours_not_in_front(driver, opened)
    try:
        for _ in range(40):  # wait for the page to load
            if 'Wikipedia' in driver.backend.window(win.id).title:
                break
            time.sleep(0.5)
        _, snap, _, _ = driver.look(win.id)
        assert any(e.role == 'Document' for e in snap.elements), 'page tree did not wake'
        start = driver.backend.window(win.id).title
        i, _ = _page_link(snap)
        result = driver.act_on_element(win.id, i)
        assert result.status == 'confirmed' and not result.focus_changed, result
        time.sleep(2)
        assert driver.backend.window(win.id).title != start
        _, snap, _, _ = driver.look(win.id)
        back = _index(snap, 'Button', 'Back')
        result = driver.act_on_element(win.id, back)
        assert result.status == 'confirmed' and not result.focus_changed, result
        time.sleep(3)
        _, snap, _, _ = driver.look(win.id)
        _, link = _page_link(snap)
        x = ((link.rect[0] + link.rect[2]) / 2 - snap.origin[0]) / snap.scale
        y = ((link.rect[1] + link.rect[3]) / 2 - snap.origin[1]) / snap.scale
        result = driver.click_point(win.id, x, y)
        assert result.status == 'confirmed' and not result.focus_changed, result
        time.sleep(3)
        _, snap, _, _ = driver.look(win.id)
        bar = _index(snap, 'Edit', 'Address and search bar')
        assert driver.set_text(win.id, 'https://example.com/', bar).status == 'confirmed'
        _assert_ours_not_in_front(driver, opened)  # Chromium grabbed focus; it was handed back
        assert driver.press_key(win.id, 'enter').status == 'needs_screen'
        assert driver.type_text(win.id, 'x').status == 'needs_screen'
    finally:
        from cognitrix.computer.windows import user32
        user32.PostMessageW(win.id, 0x0010, 0, 0)
        for _ in range(40):
            if not user32.IsWindow(win.id):
                break
            time.sleep(0.25)
        time.sleep(2)  # let the browser release its profile
        shutil.rmtree(profile, ignore_errors=True)


def test_covered_window_capture(driver, opened, tmp_path):
    """Capture shows the target even when another window is on top of it."""
    from cognitrix.computer.windows import user32
    win = driver.open(str(tmp_path))
    assert win is not None
    opened.append(win.id)
    image, snap, _, _ = driver.look(win.id)
    assert image is not None
    pixels = image.convert('L').getextrema()
    assert pixels[1] - pixels[0] > 30, 'capture looks blank'
    # The person's foreground window (if any overlaps) did not change.
    assert user32.GetForegroundWindow() not in opened
    assert re.search(r'File Explorer|' + re.escape(tmp_path.name), driver.backend.window(win.id).title)


# -- use_screen: takes the screen for a moment ---------------------------------

def _wait_until_idle(driver, seconds=5, limit=120):
    """Only take the screen while nobody is using the keyboard or mouse."""
    for _ in range(limit):
        if driver.backend.idle_seconds() >= seconds:
            return
        time.sleep(1)
    pytest.skip('the person kept using the computer; not taking the screen')


def _screen_step(driver, fn):
    """Run one use_screen action and check the pointer and the person's window come back."""
    _wait_until_idle(driver)
    fg, cursor = driver.backend.foreground(), driver.backend.cursor_pos()
    result = fn()
    assert 'screen was used' in result.message or result.status == 'failed', result
    if driver.backend.idle_seconds() > 1:  # nobody moved the mouse meanwhile
        assert driver.backend.cursor_pos() == cursor
        if fg and driver.backend.window(fg):
            assert driver.backend.foreground() == fg
    return result


def _center_of(snap, element):
    return (((element.rect[0] + element.rect[2]) / 2 - snap.origin[0]) / snap.scale,
            ((element.rect[1] + element.rect[3]) / 2 - snap.origin[1]) / snap.scale)


def test_browser_typing_with_the_screen(driver, opened, tmp_path):
    import shutil
    browser = next((p for p in _BROWSERS if os.path.exists(p)), None)
    if browser is None:
        pytest.skip('no Chromium browser installed')
    profile = tmp_path / 'profile'
    win = driver.open(browser, timeout_s=25, args=(
        f'--user-data-dir="{profile}" --no-first-run --no-default-browser-check '
        '--new-window https://en.wikipedia.org/wiki/Special:Search'))
    assert win is not None and win.kind == 'chromium'
    opened.append(win.id)
    try:
        for _ in range(40):
            if 'Search' in driver.backend.window(win.id).title:
                break
            time.sleep(0.5)
        time.sleep(2)
        # Background typing is refused, pointing at use_screen.
        refused = driver.type_text(win.id, 'x')
        assert refused.status == 'needs_screen' and 'use_screen=true' in refused.message
        _, snap, _, _ = driver.look(win.id)
        box = next(e for e in snap.elements if e.role in ('Edit', 'ComboBox') and 'earch' in e.name
                   and e.name != 'Address and search bar' and e.rect[1] > snap.origin[1] + 120)
        x, y = _center_of(snap, box)
        result = _screen_step(driver, lambda: driver.type_text(win.id, 'Background process', x, y,
                                                               use_screen=True))
        assert result.status == 'confirmed', result  # read back from the search box
        start = driver.backend.window(win.id).title
        # Submit by clicking back into the box and typing a new line (Enter).
        _, snap, _, _ = driver.look(win.id)
        box = next(e for e in snap.elements if e.role in ('Edit', 'ComboBox') and 'earch' in e.name
                   and e.name != 'Address and search bar' and e.rect[1] > snap.origin[1] + 120)
        x, y = _center_of(snap, box)
        _screen_step(driver, lambda: driver.type_text(win.id, '\n', x, y, use_screen=True))
        for _ in range(20):
            if driver.backend.window(win.id).title != start:
                break
            time.sleep(0.5)
        assert driver.backend.window(win.id).title != start  # the search ran
        # A URL typed into the address bar, submitted with a new line.
        _, snap, _, _ = driver.look(win.id)
        x, y = _center_of(snap, snap.elements[_index(snap, 'Edit', 'Address and search bar')])
        _screen_step(driver, lambda: driver.type_text(win.id, 'https://example.com/\n', x, y, use_screen=True))
        for _ in range(20):
            if 'Example Domain' in driver.backend.window(win.id).title:
                break
            time.sleep(0.5)
        assert 'Example Domain' in driver.backend.window(win.id).title
        _screen_step(driver, lambda: driver.press_key(win.id, 'alt+left', use_screen=True))
        for _ in range(20):
            if 'Example Domain' not in driver.backend.window(win.id).title:
                break
            time.sleep(0.5)
        assert 'Example Domain' not in driver.backend.window(win.id).title  # went back
    finally:
        from cognitrix.computer.windows import user32
        user32.PostMessageW(win.id, 0x0010, 0, 0)
        for _ in range(40):
            if not user32.IsWindow(win.id):
                break
            time.sleep(0.25)
        time.sleep(2)
        shutil.rmtree(profile, ignore_errors=True)


@pytest.mark.xfail(reason="A fresh VS Code profile opens onboarding pages and its file editor hides its text "
                          "from UI Automation, so the test can't reliably find and check the editor yet; "
                          "screen typing itself is covered by the browser test.", strict=False)
def test_vscode_typing_with_the_screen(driver, opened, tmp_path):
    import shutil
    code = os.path.expandvars(r'%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe')
    if not os.path.exists(code):
        pytest.skip('VS Code is not installed')
    profile, extensions, path = tmp_path / 'profile', tmp_path / 'ext', tmp_path / 'typed.txt'
    path.write_text('', encoding='utf-8')
    win = driver.open(code, timeout_s=30, args=(
        f'--user-data-dir="{profile}" --extensions-dir="{extensions}" --new-window '
        f'--disable-workspace-trust "{path}"'))
    assert win is not None and win.kind == 'chromium'
    opened.append(win.id)
    try:
        for _ in range(40):
            if 'typed.txt' in driver.backend.window(win.id).title:
                break
            time.sleep(0.5)
        time.sleep(3)
        _, snap, _, _ = driver.look(win.id)
        # A fresh profile opens with onboarding pages: click through them in the background.
        for _ in range(4):
            steps = [i for i, e in enumerate(snap.elements) if e.name.lower() in
                     ('continue without signing in', 'get started', 'skip', 'skip for now')]
            if not steps:
                break
            driver.act_on_element(win.id, steps[0])
            time.sleep(1.5)
            _, snap, _, _ = driver.look(win.id)
        # The file's editor is the biggest editor area (the chat box is another one).
        editor = max((e for e in snap.elements if e.role == 'Edit' and e.name.startswith('The editor')
                      and e.rect[2] > e.rect[0]), key=lambda e: (e.rect[2] - e.rect[0]) * (e.rect[3] - e.rect[1]))
        x, y = _center_of(snap, editor)
        result = _screen_step(driver, lambda: driver.type_text(win.id, 'Typed by Cognitrix with the screen.',
                                                               x, y, use_screen=True))
        assert result.status != 'failed', result
        _screen_step(driver, lambda: driver.press_key(win.id, 'ctrl+s', use_screen=True))
        for _ in range(20):
            if path.read_text(encoding='utf-8') == 'Typed by Cognitrix with the screen.':
                break
            time.sleep(0.5)
        assert path.read_text(encoding='utf-8') == 'Typed by Cognitrix with the screen.'
    finally:
        from cognitrix.computer.windows import user32
        user32.PostMessageW(win.id, 0x0010, 0, 0)
        for _ in range(40):
            if not user32.IsWindow(win.id):
                break
            time.sleep(0.25)
        time.sleep(2)
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(extensions, ignore_errors=True)
