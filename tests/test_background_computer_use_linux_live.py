"""Live background computer use on a real Linux (X11) desktop.

Opens GTK's widget demo, drives it in the background through the driver and
reads the results back. Opt in with COGNITRIX_LIVE_DESKTOP=1 on an X11 session
that has the accessibility bus and `gtk3-widget-factory` (Debian/Ubuntu:
gtk-3-examples, at-spi2-core); skipped everywhere else. A virtual display works:

    xvfb-run -a dbus-run-session -- sh -c 'openbox & /usr/libexec/at-spi-bus-launcher --launch-immediately &
        sleep 1; COGNITRIX_LIVE_DESKTOP=1 pytest tests/test_background_computer_use_linux_live.py'
"""
import os
import shutil
import signal
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith('linux') or os.environ.get('COGNITRIX_LIVE_DESKTOP') != '1'
    or not shutil.which('gtk3-widget-factory'),
    reason='live desktop test: Linux X11 with COGNITRIX_LIVE_DESKTOP=1 and gtk3-widget-factory only',
)


@pytest.fixture(scope='module')
def driver():
    from cognitrix.computer import Driver, Unsupported
    from cognitrix.computer.linux import LinuxBackend
    try:
        backend = LinuxBackend()
    except Unsupported as exc:
        pytest.skip(str(exc))
    if backend.atspi is None:
        pytest.skip(f'no accessibility bus: {backend.atspi_error}')
    return Driver(backend)


@pytest.fixture(scope='module')
def factory(driver):
    """The widget demo, with a second window opened on top of it."""
    before = {w.id for w in driver.list_windows()}
    win = driver.open('gtk3-widget-factory')
    assert win is not None, 'the widget demo did not open'
    cover = driver.open('gtk3-widget-factory')
    assert cover is not None and cover.id != win.id
    driver.backend.give_back_focus(cover.id)
    time.sleep(0.5)
    yield win
    for w in driver.list_windows():
        if w.id not in before and w.app == 'gtk3-widget-factory':
            try:
                os.kill(w.pid, signal.SIGTERM)
            except OSError:
                pass


def _find(snap, role, name=None, where=None):
    for i, e in enumerate(snap.elements):
        if e.role == role and e.enabled and (name is None or e.name == name) and (where is None or where(e)):
            return i
    raise AssertionError(f'{role} {name!r} not found among {len(snap.elements)} controls')


def test_reads_a_covered_window(driver, factory):
    image, snap, shown, _ = driver.look(factory.id, viewer='t')
    assert image is not None and image.size[0] > 400
    # Covered pixels are still the app's own, not black and not the window on top.
    colours = image.convert('L').getextrema()
    assert colours[1] > 200
    dark = sum(1 for p in image.convert('L').resize((64, 36)).getdata() if p < 10)
    assert dark < 64 * 36 * 0.2
    assert any(e.role == 'check box' for e in snap.elements)
    assert driver.backend.foreground() != factory.id


def test_presses_and_fills_controls_without_coming_forward(driver, factory):
    from cognitrix.computer import CONFIRMED
    front = driver.backend.foreground()
    _, snap, _, _ = driver.look(factory.id, viewer='t')

    box = _find(snap, 'check box', 'checkbutton', lambda e: e.state.get('checked') is False)
    result = driver.act_on_element(factory.id, box, viewer='t')
    assert result.status == CONFIRMED and 'checked=True' in result.message

    field = _find(snap, 'text', None, lambda e: e.value == 'entry')
    assert driver.set_text(factory.id, 'set in the background', field, viewer='t').status == CONFIRMED

    e = snap.elements[field]
    x = ((e.rect[0] + e.rect[2]) // 2 - snap.origin[0]) / snap.scale
    y = ((e.rect[1] + e.rect[3]) // 2 - snap.origin[1]) / snap.scale
    typed = driver.type_text(factory.id, ' and typed', x, y, viewer='t')
    assert typed.status == CONFIRMED
    assert driver.backend.element_state(e).value == 'set in the background and typed'

    slider = _find(snap, 'slider')
    assert driver.set_text(factory.id, '75', slider, viewer='t').status == CONFIRMED

    page = _find(snap, 'radio button', 'Page 2')
    assert driver.act_on_element(factory.id, page, viewer='t').status == CONFIRMED
    _, after, _, _ = driver.look(factory.id, viewer='t')
    assert [e.name for e in after.elements] != [e.name for e in snap.elements]
    driver.act_on_element(factory.id, _find(after, 'radio button', 'Page 1'), viewer='t')

    assert driver.backend.foreground() == front, 'the window came to the front'


def test_terminals_are_never_controlled(driver):
    from cognitrix.computer.linux import LinuxBackend
    assert 'terminal' in LinuxBackend._blocked(driver.backend, 'xterm', '', 1, False)
    assert 'password' in LinuxBackend._blocked(driver.backend, 'keepassxc', '', 1, False)
    assert LinuxBackend._blocked(driver.backend, 'gedit', '', 1, False) is None
