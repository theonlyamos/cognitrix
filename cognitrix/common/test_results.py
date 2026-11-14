"""Read a test run's output: how many passed, failed and were skipped, and
which tests failed. Knows pytest, unittest, Jest, Vitest, Mocha, Go, Cargo,
RSpec and PHPUnit summaries; anything else is judged by the exit code."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


@dataclass
class TestSummary:
    framework: str = ''
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    errors: int | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)  # (test, message)
    seconds: float | None = None

    @property
    def known(self) -> bool:
        return self.passed is not None or self.failed is not None

    @property
    def broken(self) -> int:
        return (self.failed or 0) + (self.errors or 0)

    def add(self, name: str, message: str = '') -> None:
        name = name.strip()[:200]
        if name and all(name != n for n, _ in self.failures) and len(self.failures) < 50:
            self.failures.append((name, message.strip()[:300]))


def _num(pattern: str, text: str) -> int | None:
    found = re.findall(pattern, text, re.M)
    if not found:
        return None
    return sum(int(x) for x in found)


def _last_num(pattern: str, text: str) -> int | None:
    found = re.findall(pattern, text, re.M)
    return int(found[-1]) if found else None


def parse(output: str) -> TestSummary:
    text = _ANSI.sub('', output or '')
    s = TestSummary()

    # pytest: "==== 2 failed, 10 passed, 1 skipped in 1.23s ====" or "5 passed in 0.10s"
    py = re.findall(r'^=*\s*((?:\d+ (?:passed|failed|skipped|errors?|xfailed|xpassed|warnings?|deselected)'
                    r'(?:, )?)+)\s+in ([\d.]+)s', text, re.M)
    if py:
        line, secs = py[-1]
        s.framework = 'pytest'
        s.passed = _num(r'(\d+) passed', line) or 0
        s.failed = _num(r'(\d+) failed', line) or 0
        s.skipped = _num(r'(\d+) skipped', line) or 0
        s.errors = _num(r'(\d+) errors?', line) or 0
        s.seconds = float(secs)
        for m in re.finditer(r'^(FAILED|ERROR) (\S+)(?: - (.*))?$', text, re.M):
            s.add(m.group(2), m.group(3) or '')
        return s

    # unittest: "Ran 12 tests in 0.01s" + "OK" / "FAILED (failures=1, errors=2)"
    ran = re.search(r'^Ran (\d+) tests? in ([\d.]+)s', text, re.M)
    if ran:
        s.framework = 'unittest'
        total = int(ran.group(1))
        s.seconds = float(ran.group(2))
        verdict = text[ran.end():]
        s.failed = _last_num(r'failures=(\d+)', verdict) or 0
        s.errors = _last_num(r'errors=(\d+)', verdict) or 0
        s.skipped = _last_num(r'skipped=(\d+)', verdict) or 0
        s.passed = max(0, total - s.failed - s.errors - s.skipped)
        for m in re.finditer(r'^(?:FAIL|ERROR): (.+)$', text, re.M):
            s.add(m.group(1))
        return s

    # Jest: "Tests:       1 failed, 4 passed, 5 total"
    jest = re.findall(r'^Tests:\s+(.+total)', text, re.M)
    if jest:
        line = jest[-1]
        s.framework = 'jest'
        s.passed = _num(r'(\d+) passed', line) or 0
        s.failed = _num(r'(\d+) failed', line) or 0
        s.skipped = (_num(r'(\d+) skipped', line) or 0) + (_num(r'(\d+) todo', line) or 0)
        t = re.search(r'^Time:\s+([\d.]+)\s*s', text, re.M)
        s.seconds = float(t.group(1)) if t else None
        for m in re.finditer(r'^\s*● (.+?)$', text, re.M):
            if '›' in m.group(1) or not m.group(1).startswith('Test suite failed'):
                s.add(m.group(1))
        return s

    # Vitest: " Tests  1 failed | 4 passed (5)"
    vi = re.findall(r'^\s*Tests\s+(.+?)\((\d+)\)\s*$', text, re.M)
    if vi:
        line = vi[-1][0]
        s.framework = 'vitest'
        s.passed = _num(r'(\d+) passed', line) or 0
        s.failed = _num(r'(\d+) failed', line) or 0
        s.skipped = (_num(r'(\d+) skipped', line) or 0) + (_num(r'(\d+) todo', line) or 0)
        d = re.search(r'^\s*Duration\s+([\d.]+)(m?s)', text, re.M)
        if d:
            s.seconds = float(d.group(1)) / (1000 if d.group(2) == 'ms' else 1)
        for m in re.finditer(r'^\s*(?:FAIL|×|✗)\s+(.+?)\s*$', text, re.M):
            s.add(m.group(1))
        return s

    # Mocha: "  4 passing (10ms)", "  1 failing", "  2 pending"
    passing = re.search(r'^\s+(\d+) passing \((\d+)(ms|s|m)\)', text, re.M)
    if passing:
        s.framework = 'mocha'
        s.passed = int(passing.group(1))
        s.failed = _last_num(r'^\s+(\d+) failing', text) or 0
        s.skipped = _last_num(r'^\s+(\d+) pending', text) or 0
        unit = {'ms': 0.001, 's': 1, 'm': 60}[passing.group(3)]
        s.seconds = int(passing.group(2)) * unit
        for m in re.finditer(r'^\s+\d+\) (.+)$', text[passing.end():], re.M):
            s.add(m.group(1))
        return s

    # Cargo: "test result: FAILED. 3 passed; 1 failed; 0 ignored; ..." (one per test binary)
    cargo = re.findall(r'^test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored', text, re.M)
    if cargo:
        s.framework = 'cargo'
        s.passed = sum(int(p) for p, _, _ in cargo)
        s.failed = sum(int(f) for _, f, _ in cargo)
        s.skipped = sum(int(i) for _, _, i in cargo)
        for m in re.finditer(r'^test (\S+) \.\.\. FAILED$', text, re.M):
            s.add(m.group(1))
        return s

    # Go: "--- FAIL: TestX (0.00s)" / "--- PASS: ..." with -v, else "ok"/"FAIL" per package
    if re.search(r'^(?:--- (?:PASS|FAIL|SKIP): |ok\s+\S+\s+[\d.]+s|FAIL\s+\S+\s+[\d.]+s)', text, re.M):
        s.framework = 'go'
        passed = len(re.findall(r'^\s*--- PASS: ', text, re.M))
        failed = re.findall(r'^\s*--- FAIL: (\S+)', text, re.M)
        skipped = len(re.findall(r'^\s*--- SKIP: ', text, re.M))
        if passed or failed or skipped:
            s.passed, s.failed, s.skipped = passed, len(failed), skipped
            for name in failed:
                s.add(name)
        else:  # package level only
            s.passed = len(re.findall(r'^ok\s+\S+', text, re.M))
            pkgs = re.findall(r'^FAIL\s+(\S+)\s+[\d.]+s', text, re.M)
            s.failed = len(pkgs)
            for name in pkgs:
                s.add(name)
        return s

    # RSpec: "10 examples, 2 failures, 1 pending"
    rspec = re.search(r'^(\d+) examples?, (\d+) failures?(?:, (\d+) pending)?', text, re.M)
    if rspec:
        s.framework = 'rspec'
        total, failed = int(rspec.group(1)), int(rspec.group(2))
        pending = int(rspec.group(3) or 0)
        s.failed, s.skipped, s.passed = failed, pending, max(0, total - failed - pending)
        for m in re.finditer(r'^rspec (\S+) # (.+)$', text, re.M):
            s.add(m.group(2), m.group(1))
        return s

    # PHPUnit: "OK (5 tests, 9 assertions)" / "Tests: 5, Assertions: 9, Failures: 1."
    php_ok = re.search(r'^OK \((\d+) tests?', text, re.M)
    php = re.search(r'^Tests: (\d+), Assertions: \d+(.*)$', text, re.M)
    if php_ok or php:
        s.framework = 'phpunit'
        if php_ok:
            s.passed, s.failed = int(php_ok.group(1)), 0
        else:
            total = int(php.group(1))
            rest = php.group(2)
            s.failed = _num(r'Failures: (\d+)', rest) or 0
            s.errors = _num(r'Errors: (\d+)', rest) or 0
            s.skipped = (_num(r'Skipped: (\d+)', rest) or 0) + (_num(r'Incomplete: (\d+)', rest) or 0)
            s.passed = max(0, total - s.failed - s.errors - s.skipped)
        return s
    return s


def card(summary: TestSummary, *, command: str, exit_code: int | None, timed_out: bool, seconds: float | None,
         images: list[tuple[str, str]] | None = None, where: str = '') -> dict:
    """A Show Card style card for the chat (cognitrix/cards short form)."""
    if timed_out:
        title, ok = 'Tests timed out', False
    elif summary.known and summary.broken:
        n = summary.broken
        title, ok = f'{n} test{"s" if n != 1 else ""} failed', False
    elif summary.known and exit_code == 0:
        title, ok = 'All tests passed', True
    elif summary.known and summary.passed and exit_code not in (0, None):
        title, ok = 'Tests passed, but the run failed', False
    elif exit_code == 0:
        title, ok = 'Tests passed', True
    else:
        title, ok = f'Tests failed (exit code {exit_code})', False
    fields = []
    if summary.known:
        fields.append({'label': 'Passed', 'value': str(summary.passed or 0), 'tone': 'good' if summary.passed else 'neutral'})
        fields.append({'label': 'Failed', 'value': str(summary.broken), 'tone': 'bad' if summary.broken else 'neutral'})
        if summary.skipped:
            fields.append({'label': 'Skipped', 'value': str(summary.skipped), 'tone': 'neutral'})
    else:
        fields.append({'label': 'Result', 'value': 'Passed' if ok else 'Failed', 'tone': 'good' if ok else 'bad'})
    took = summary.seconds if summary.seconds is not None else seconds
    if took is not None:
        fields.append({'label': 'Time', 'value': _duration(took), 'tone': 'neutral'})
    out: dict = {'title': title, 'subtitle': _shorten(f'{command}' + (f'  ·  {where}' if where else ''), 140),
                 'fields': fields}
    if summary.failures:
        rows = [[name, msg] for name, msg in summary.failures[:20]]
        out['table'] = {'title': 'Failures' + (f' (first 20 of {len(summary.failures)})'
                                               if len(summary.failures) > 20 else ''),
                        'columns': ['Test', 'Message'], 'rows': rows}
    if images:
        out['images'] = [{'url': url, 'alt': alt} for url, alt in images]
    return out


def _duration(seconds: float) -> str:
    if seconds < 1:
        return f'{int(seconds * 1000)} ms'
    if seconds < 90:
        return f'{seconds:.1f} s'
    return f'{int(seconds // 60)} min {int(seconds % 60)} s'


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + '…'
