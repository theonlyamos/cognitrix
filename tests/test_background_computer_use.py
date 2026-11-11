"""Background computer use driver logic, with a fake backend (runs anywhere)."""
import threading
import time

import pytest
from PIL import Image

import cognitrix.computer as computer
from cognitrix.computer import (
    CONFIRMED,
    FAILED,
    NEEDS_SCREEN,
    UNVERIFIED,
    Blocked,
    Busy,
    Driver,
    Element,
    Stale,
    WindowInfo,
    blocked_reason,
    fingerprint_of,
)
from cognitrix.tools import computer as tools
from cognitrix.tools import misc


class FakeBackend:
    def __init__(self):
        self.windows = {
            1: WindowInfo(1, 'Calc', 'calc.exe', 10, rect=(100, 50, 2660, 1650), kind='uwp'),
            2: WindowInfo(2, 'Notes', 'notepad.exe', 11, rect=(0, 0, 800, 600)),
            3: WindowInfo(3, 'Code', 'code.exe', 12, rect=(0, 0, 800, 600), kind='chromium'),
            4: WindowInfo(4, 'PowerShell', 'windowsterminal.exe', 13,
                          blocked=blocked_reason('windowsterminal.exe')),
        }
        self.trees = {
            1: [Element('Button', 'One', actions=['invoke'], rect=(110, 60, 210, 160)),
                Element('Text', 'Display is 0'),
                Element('Button', 'Mute', actions=['toggle'], state={'toggled': 'off'}, rect=(300, 60, 400, 160)),
                Element('MenuItem', 'Save', actions=['invoke'], accelerator='Ctrl+S')],
            2: [Element('Document', 'Text editor', value='', actions=['set_value'])],
            3: [Element('Button', 'Search', actions=['invoke'])],
            4: [],
        }
        self.fg = 99
        self.posted = []
        self.effects = {}  # (window, element name) -> callable run on perform
        self.accept_value = True
        self.type_lands = True

    def list_windows(self):
        return list(self.windows.values())

    def window(self, wid):
        return self.windows.get(wid)

    def foreground(self):
        return self.fg

    def launch(self, target):
        self.windows[5] = WindowInfo(5, target, 'new.exe', 14)
        self.trees[5] = []

    def prepare(self, wid):
        return ''

    def capture(self, wid):
        w = self.windows[wid]
        return Image.new('RGB', (w.rect[2] - w.rect[0], w.rect[3] - w.rect[1]), 'white'), w.rect[:2], ''

    def elements(self, wid):
        return [Element(e.role, e.name, e.value, e.enabled, list(e.actions), e.rect, dict(e.state),
                        e.accelerator, ref=(wid, e.name)) for e in self.trees[wid]]

    def _find(self, ref):
        wid, name = ref
        return next((e for e in self.trees.get(wid, []) if e.name == name), None)

    def element_state(self, element):
        e = self._find(element.ref)
        if e is None:
            return None
        return Element(e.role, e.name, e.value, e.enabled, list(e.actions), e.rect, dict(e.state),
                       e.accelerator, ref=element.ref)

    def perform(self, element, action, value=None):
        e = self._find(element.ref)
        if action == 'toggle':
            e.state['toggled'] = 'on' if e.state['toggled'] == 'off' else 'off'
        elif action == 'set_value' and self.accept_value:
            e.value = value
        effect = self.effects.get(element.ref)
        if effect:
            effect()

    def focused_element(self, wid):
        editable = [e for e in self.elements(wid) if 'set_value' in e.actions]
        return editable[0] if editable else None

    def post_click(self, wid, x, y, button, double):
        self.posted.append(('click', wid, x, y, button, double))

    def post_text(self, wid, text):
        self.posted.append(('text', wid, text))
        if self.type_lands:
            doc = self.trees[wid][0]
            doc.value = (doc.value or '') + text

    def post_key(self, wid, key, mods=()):
        self.posted.append(('key', wid, key, tuple(mods)))

    def post_scroll(self, wid, x, y, amount, horizontal=False):
        self.posted.append(('scroll', wid, x, y, amount, horizontal))

    def fingerprint(self, wid):
        return fingerprint_of(self.windows[wid].title, self.trees[wid])

    def give_back_focus(self, wid):
        self.fg = wid
        return True


@pytest.fixture
def fake():
    return FakeBackend()


@pytest.fixture
def driver(fake):
    d = Driver(fake)
    d.settle_s = 0
    d.open_watch_s = 0
    return d


async def test_tools_say_so_where_there_is_no_driver(monkeypatch):
    monkeypatch.setattr(computer.sys, 'platform', 'sunos5')
    monkeypatch.setattr(computer, '_driver', None)
    out = (await tools.list_windows.run()).content
    assert 'only available on Windows and Linux' in out


async def test_linux_without_a_desktop_session_says_so(monkeypatch):
    monkeypatch.setattr(computer.sys, 'platform', 'linux')
    monkeypatch.setattr(computer, '_driver', None)
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    monkeypatch.delenv('XDG_SESSION_TYPE', raising=False)
    assert 'needs a desktop session' in (await tools.list_windows.run()).content
    monkeypatch.setenv('XDG_SESSION_TYPE', 'wayland')
    assert 'Wayland' in (await tools.list_windows.run()).content


def test_element_numbers_are_tied_to_the_latest_snapshot(driver, fake):
    _, first, _, _ = driver.look(1)
    _, second, _, _ = driver.look(1)
    with pytest.raises(Stale):
        driver.act_on_element(1, 0, snapshot_id=first.id)
    assert driver.act_on_element(1, 2, 'toggle', snapshot_id=second.id).status == CONFIRMED
    fake.trees[1] = [e for e in fake.trees[1] if e.name != 'Mute']
    with pytest.raises(Stale):
        driver.act_on_element(1, 2, 'toggle')


def test_acting_before_looking_is_refused(driver):
    with pytest.raises(Stale):
        driver.act_on_element(1, 0)


def test_verification_outcomes(driver, fake):
    driver.look(1)
    # A visible change is confirmed; nothing changing is only "unverified".
    def bump():
        fake.trees[1][1].name = 'Display is 1'
    fake.effects[(1, 'One')] = bump
    assert driver.act_on_element(1, 0).status == CONFIRMED
    fake.effects.clear()
    assert driver.act_on_element(1, 0).status == UNVERIFIED
    # set_text reads the value back.
    driver.look(2)
    assert driver.set_text(2, 'hello', 0).status == CONFIRMED
    fake.accept_value = False
    assert driver.set_text(2, 'other', 0).status == FAILED


def test_typing_that_does_not_land_fails(driver, fake):
    fake.type_lands = False
    result = driver.type_text(2, 'hi there')
    assert result.status == FAILED
    fake.type_lands = True
    assert driver.type_text(2, 'hi there').status == CONFIRMED


def test_typing_into_chromium_needs_the_screen(driver, fake):
    result = driver.type_text(3, 'hello')
    assert result.status == NEEDS_SCREEN
    assert not any(p[0] == 'text' for p in fake.posted)


def test_shortcut_uses_matching_menu_command(driver, fake):
    def saved():
        fake.windows[1].title = 'Calc saved'
    fake.effects[(1, 'Save')] = saved
    assert driver.press_key(1, 'ctrl+s').status == CONFIRMED
    assert not fake.posted  # the menu command was used, not keystrokes
    # No matching command: the shortcut is posted with ctrl held for that app only.
    assert driver.press_key(2, 'ctrl+q').status == UNVERIFIED
    assert fake.posted[-1] == ('key', 2, 'q', ('ctrl',))


def test_blocked_windows_are_refused(driver, fake):
    with pytest.raises(Blocked):
        driver.type_text(4, 'dir')
    assert blocked_reason('KeePassXC.exe') == 'it is a password manager'
    assert 'administrator' in blocked_reason('app.exe', elevated=True)
    assert "Cognitrix's own" in blocked_reason('cognitrix-desktop.exe')
    assert 'terminal' in blocked_reason('x.exe', kind='console')
    assert blocked_reason('notepad.exe') is None


async def test_list_shows_why_a_window_cant_be_controlled(driver, monkeypatch):
    monkeypatch.setattr(tools, 'get_driver', lambda: driver)
    out = (await tools.list_windows.run()).content
    assert "4: 'PowerShell' (windowsterminal.exe) - can't control: it is a terminal" in out
    assert "1: 'Calc' (calc.exe)" in out


def test_one_action_per_window_at_a_time(driver, fake):
    driver.look(1)
    driver.look(2)
    driver.lock_timeout_s = 0.2
    lock = driver._lock(1)
    lock.acquire()
    try:
        with pytest.raises(Busy):
            driver.act_on_element(1, 0)
        # Another window is not held up.
        assert driver.set_text(2, 'ok', 0).status == CONFIRMED
    finally:
        lock.release()
    started = threading.Event()

    def slow():
        started.set()
        time.sleep(0.3)
    fake.effects[(1, 'One')] = slow
    driver.lock_timeout_s = 5
    worker = threading.Thread(target=driver.act_on_element, args=(1, 0))
    worker.start()
    started.wait(2)
    t0 = time.monotonic()
    driver.act_on_element(1, 2, 'toggle')
    assert time.monotonic() - t0 > 0.1  # waited for the first action
    worker.join()


def test_pixel_coordinates_scale_back_to_the_window(driver, fake):
    image, snap, _, _ = driver.look(1)  # 2560x1600 window -> shown at 1280x800
    assert image.size == (1280, 800) and snap.scale == 2
    driver.click_point(1, 600, 400)  # nothing with an action there -> posted click
    assert fake.posted[-1] == ('click', 1, 100 + 1200, 50 + 800, 'left', False)
    # A point over a control uses the control's pattern instead.
    fake.posted.clear()
    driver.click_point(1, 125, 40)  # screen (350, 130) is inside "Mute"
    assert not fake.posted
    assert fake.trees[1][2].state['toggled'] == 'on'


def test_gemini_grid_coordinates_are_converted(driver, fake, monkeypatch):
    monkeypatch.setattr(tools, 'get_driver', lambda: driver)
    driver.look(1)
    token = misc.coords_normalized.set(True)
    try:
        assert tools._image_point(driver, 1, 500, 500) == (640, 400)
    finally:
        misc.coords_normalized.reset(token)


def test_focus_change_is_reported(driver, fake):
    driver.look(1)

    def steal():
        fake.fg = 1
    fake.effects[(1, 'One')] = steal
    result = driver.act_on_element(1, 0)
    assert result.focus_changed
    assert 'foreground window changed' in result.text()
    # The app grabbed focus, so it is handed back to the person's window.
    assert fake.fg == 99 and 'handed back' in result.message


def test_listeners_get_screen_points(driver, fake):
    events = []
    driver.add_listener(events.append)
    driver.look(1)
    driver.click_point(1, 600, 400)
    assert events[-1].window_id == 1 and (events[-1].x, events[-1].y) == (1300, 850)


def test_open_finds_the_new_window(driver, fake):
    win = driver.open('report.txt', timeout_s=2)
    assert win is not None and win.id == 5


async def test_look_tool_queues_the_screenshot(driver, fake, monkeypatch):
    monkeypatch.setattr(tools, 'get_driver', lambda: driver)
    token, images = misc.start_image_collection()
    try:
        out = (await tools.look_at_window.run(window_id=1)).content
    finally:
        misc.end_image_collection(token)
    assert len(images) == 1 and 'snapshot s' in out
    assert "[0] Button 'One' actions=invoke at (" in out


def test_tools_are_registered_and_classified():
    from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel
    from cognitrix.tools.base import ToolManager

    detector = DestructiveOpDetector()
    for name in ('list_windows', 'look_at_window', 'scroll_window'):
        assert ToolManager.get_by_name(name) is not None, name
        assert detector.analyze(name, {}).risk_level is RiskLevel.LOW
    for name in ('open_app', 'click_element', 'set_text', 'click_in_window', 'type_in_window', 'press_key_in_window'):
        assert ToolManager.get_by_name(name) is not None, name
        assert detector.analyze(name, {}).risk_level is RiskLevel.MEDIUM


def test_another_viewers_look_makes_numbers_stale(driver, fake):
    driver.look(1, viewer='agent-a')
    driver.look(1, viewer='agent-b')
    with pytest.raises(Stale):
        driver.act_on_element(1, 0, viewer='agent-a')
    with pytest.raises(Stale):
        driver.click_point(1, 5, 5, viewer='agent-a')
    assert driver.act_on_element(1, 0, viewer='agent-b').status in (CONFIRMED, UNVERIFIED)
    driver.look(1, viewer='agent-a')
    assert driver.act_on_element(1, 0, viewer='agent-a').status in (CONFIRMED, UNVERIFIED)


def test_pixel_click_follows_a_moved_window(driver, fake):
    driver.look(2)
    w = fake.windows[2]
    fake.windows[2] = WindowInfo(2, w.title, w.app, w.pid, rect=(500, 400, 1300, 1000))
    fake.trees[2] = []  # nothing to hit-test, so the click is posted
    driver.click_point(2, 20, 30, 'right')
    assert fake.posted[-1][:4] == ('click', 2, 520, 430)
    # A resized window needs a fresh look.
    fake.windows[2] = WindowInfo(2, w.title, w.app, w.pid, rect=(500, 400, 1400, 1000))
    with pytest.raises(Stale):
        driver.click_point(2, 20, 30, 'right')


def test_fallback_scroll_keeps_its_axis(driver, fake):
    fake.trees[2] = []
    driver.look(2)
    driver.scroll(2, 'down', 2)
    assert fake.posted[-1][4:] == (-2, False)
    driver.scroll(2, 'up', 2)
    assert fake.posted[-1][4:] == (2, False)
    driver.scroll(2, 'right', 2)
    assert fake.posted[-1][4:] == (2, True)
    driver.scroll(2, 'left', 2)
    assert fake.posted[-1][4:] == (-2, True)


def test_linux_reads_shortcuts_from_accessibility_key_bindings():
    from cognitrix.computer.linux import _accelerator
    assert _accelerator('s;<Alt>f:s;<Control>s') == 'ctrl+s'
    assert _accelerator('<Primary><Shift>b') == 'ctrl+shift+b'
    assert _accelerator('<Alt>F4') == 'alt+f4'
    assert _accelerator('n') == '' and _accelerator('') == ''
