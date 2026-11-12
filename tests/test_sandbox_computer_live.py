"""Live sandboxed computer: a real container, two agents on two desktops.

Opt in with COGNITRIX_LIVE_SANDBOX=1 where Docker runs and the image is built:

    docker build -f sandbox/desktop/Dockerfile -t cognitrix-desktop:latest .
    COGNITRIX_LIVE_SANDBOX=1 pytest tests/test_sandbox_computer_live.py

Set COGNITRIX_SANDBOX_IMAGE to test another tag. The test removes its
container and volume afterwards.
"""
import os
import shutil
import subprocess
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('COGNITRIX_LIVE_SANDBOX') != '1' or not shutil.which('docker'),
    reason='live sandbox test: needs Docker and COGNITRIX_LIVE_SANDBOX=1',
)

USER = 'live-sandbox-test'
PAGE = ('<title>T</title><input aria-label="Email">'
        '<button onclick="document.title=\'got \'+document.querySelector(\'input\').value">Go</button>')


@pytest.fixture(scope='module')
def manager():
    os.environ['COGNITRIX_COMPUTER_SANDBOX'] = 'docker'
    from cognitrix.computer.sandbox.client import SandboxManager

    mgr = SandboxManager(image=os.environ.get('COGNITRIX_SANDBOX_IMAGE', 'cognitrix-desktop:latest'))
    mgr.reset(USER)
    yield mgr
    mgr.reset(USER)


def _fill_and_submit(mgr, agent: str, browser: str) -> str:
    from cognitrix.computer import Actor, current_actor

    current_actor.set(Actor(agent_id=agent, agent=agent, user_id=USER))
    driver = mgr.driver(USER, agent)
    subprocess.run(['docker', 'exec', '-i', mgr.container_name(USER), 'sh', '-c', 'cat > /home/agent/page.html'],
                   input=PAGE.encode(), check=True)
    win = driver.open(browser, args='file:///home/agent/page.html', timeout_s=60)
    assert win is not None, f'{browser} did not open'
    for _ in range(10):
        time.sleep(2)
        _, snap, _, _ = driver.look(win.id, viewer=agent)
        names = {e.name: i for i, e in enumerate(snap.elements)}
        if 'Go' in names and 'Email' in names:
            break
    else:
        pytest.fail(f'{browser}: the page controls never showed up')
    driver.set_text(win.id, f'{agent}@example.com', index=names['Email'], snapshot_id=snap.id, viewer=agent)
    _, snap, _, _ = driver.look(win.id, viewer=agent)
    names = {e.name: i for i, e in enumerate(snap.elements)}
    driver.act_on_element(win.id, names['Go'], snapshot_id=snap.id, viewer=agent)
    time.sleep(1)
    return driver.backend.window(win.id).title


def test_two_agents_two_desktops(manager):
    titles: dict[str, str] = {}

    def run(agent, browser):
        titles[agent] = _fill_and_submit(manager, agent, browser)

    threads = [threading.Thread(target=run, args=('agent-a', 'chromium')),
               threading.Thread(target=run, args=('agent-b', 'firefox'))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(240)
    assert titles['agent-a'].startswith('got agent-a@example.com')
    assert titles['agent-b'].startswith('got agent-b@example.com')
    names = {d['name'] for d in manager.sandbox(USER).desktops()}
    assert {'agent-a', 'agent-b'} <= names


def test_take_over_pauses_the_agent(manager):
    from cognitrix.computer import HumanControl

    box = manager.sandbox(USER)
    driver = manager.driver(USER, 'agent-a')
    out = box.request('POST', '/desktops/agent-a/hold', {'by': 'Tester'})
    assert out['ok'] and out['vnc_password']
    try:
        with pytest.raises(HumanControl):
            driver.list_windows()  # listing is allowed; acting and looking wait
            driver.look(driver.list_windows()[0].id)
    finally:
        box.request('POST', '/desktops/agent-a/release')
