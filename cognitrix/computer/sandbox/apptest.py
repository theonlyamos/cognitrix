"""Click through an app the way a person would and say what broke.

Test App hands a list of steps (open a page, click, fill in, press a key,
check what's on screen) to :func:`run_steps`, which plays them in a headless
browser (see :mod:`pagecheck` for how the browser is found and driven) and
reports each step: passed or why not, plus the script errors, console errors,
failed requests and dialogs it caused. It stops at the first failed step, with
a screenshot, the start of the page's text and the buttons and fields on it, so
the agent can fix the app or the step.

Clicks and typing go through the browser's real input (``Input.*``), so a button
hidden behind a dialog or a disabled one fails like it would for a person.

A step is a small JSON object with one action:

    {"go": "settings"}                     open a page of the app (relative path)
    {"click": "Add item"}                  a button, link or anything with that text
    {"fill": "Title", "with": "Milk"}      a field found by label, placeholder or name
    {"select": "Priority", "option": "High"}
    {"check": "Done"} / {"uncheck": "Done"}
    {"press": "Enter"}                     optionally "on": a field to focus first
    {"hover": "Menu"}
    {"see": "Milk"} / {"not_see": "Error"} text on the page (waits for it)
    {"expect": "#list li", "count": 3}     also "text", "value", "checked", "enabled"
    {"url": "items?id="}                   the address contains this
    {"wait": 500} / {"wait": ".loaded"}    milliseconds, or until something shows
    {"reload": true}                       e.g. to see data was saved
    {"js": "document.title.length > 0"}    must come out truthy
    {"screenshot": "after adding"}

Targets are a CSS selector, else the visible text (``text=...`` / ``css=...``
force one). Every step may add ``"timeout"`` (seconds, default 5) and
``"dialog"`` ("accept", "dismiss" or text to type into a prompt).

Standard library only, like :mod:`pagecheck`: the sandbox image ships it alone.
"""
from __future__ import annotations

import json
import time
import urllib.parse

from cognitrix.computer.sandbox.pagecheck import (
    PREFIX,
    PageCheckError,
    _Hosting,
    _Page,
    _short,
    _summarise,
    find_browser,
)

MAX_STEPS = 60
MAX_SHOTS = 4
DEFAULT_WAIT = 5.0
MAX_WAIT = 30.0
BUDGET = 240.0
"""Seconds a whole run may take."""

ACTIONS = ('go', 'click', 'fill', 'type', 'select', 'check', 'uncheck', 'press', 'hover', 'see', 'not_see',
           'expect', 'url', 'wait', 'reload', 'js', 'screenshot')

KEYS = {
    'enter': ('Enter', 'Enter', 13, '\r'), 'tab': ('Tab', 'Tab', 9, ''), 'escape': ('Escape', 'Escape', 27, ''),
    'esc': ('Escape', 'Escape', 27, ''), 'backspace': ('Backspace', 'Backspace', 8, ''),
    'delete': ('Delete', 'Delete', 46, ''), 'space': (' ', 'Space', 32, ' '),
    'arrowup': ('ArrowUp', 'ArrowUp', 38, ''), 'arrowdown': ('ArrowDown', 'ArrowDown', 40, ''),
    'arrowleft': ('ArrowLeft', 'ArrowLeft', 37, ''), 'arrowright': ('ArrowRight', 'ArrowRight', 39, ''),
    'home': ('Home', 'Home', 36, ''), 'end': ('End', 'End', 35, ''), 'pageup': ('PageUp', 'PageUp', 33, ''),
    'pagedown': ('PageDown', 'PageDown', 34, ''),
}

# Runs in the page. ``op`` is what to do, ``a`` its arguments. Returns
# {ok, ...} or {ok: false, wait: true, msg} while the page isn't there yet.
_HELPER = r'''
(function (op, a) {
  const norm = s => String(s == null ? '' : s).replace(/\s+/g, ' ').trim().toLowerCase();
  const shown = el => {
    if (!el || !el.isConnected || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  };
  const FIELD = 'input:not([type=hidden]), textarea, select, [contenteditable=""], [contenteditable="true"]';
  const isField = el => el.matches && el.matches(FIELD);
  const INTERACTIVE = 'a, button, input, select, textarea, summary, label, [role=button], [role=link], '
    + '[role=tab], [role=menuitem], [role=checkbox], [role=option], [onclick], [tabindex]';
  const describe = el => {
    if (!el) return '';
    let d = el.tagName.toLowerCase();
    if (el.id) d += '#' + el.id;
    const t = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.name || '')
      .replace(/\s+/g, ' ').trim();
    return t ? d + ' "' + t.slice(0, 50) + '"' : d;
  };
  const strings = (el, field) => {
    const out = [el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('placeholder'),
      el.getAttribute('alt')];
    if (field) {
      out.push(el.getAttribute('name'), el.id);
      for (const l of (el.labels || [])) out.push(l.innerText);
      const by = el.getAttribute('aria-labelledby');
      if (by) for (const id of by.split(/\s+/)) { const l = document.getElementById(id); if (l) out.push(l.innerText); }
    } else {
      if (el.matches('input[type=submit], input[type=button], input[type=reset]')) out.push(el.value);
      out.push(el.innerText);
    }
    return out.filter(Boolean).map(norm);
  };
  // Elements the target names, best first.
  const find = (target, kind) => {
    let t = String(target || '');
    let how = 'any';
    if (/^css=/i.test(t)) { how = 'css'; t = t.slice(4); } else if (/^text=/i.test(t)) { how = 'text'; t = t.slice(5); }
    if (how !== 'text') {
      try {
        const hits = [...document.querySelectorAll(t)];
        const seen = hits.filter(shown);
        if (seen.length || how === 'css') return {els: seen.length ? seen : hits, by: 'css', hidden: !seen.length};
      } catch (e) { if (how === 'css') return {els: [], by: 'css', bad: String(e.message || e)}; }
    }
    const want = norm(t);
    const field = kind === 'field';
    const pool = [...document.body.querySelectorAll(field ? FIELD + ', label' : '*')]
      .filter(el => shown(el) || (field && isField(el) && el.labels && [...el.labels].some(shown)));
    const pick = test => {
      let m = pool.filter(el => strings(el, field && isField(el)).some(test));
      // Keep the innermost: a button, not the card around it.
      m = m.filter(el => !m.some(o => o !== el && el.contains(o)));
      if (field) m = m.map(el => isField(el) ? el : (el.control || el.querySelector(FIELD))).filter(Boolean);
      if (kind === 'click') m = m.map(el => el.closest(INTERACTIVE) || el);
      return [...new Set(m)];
    };
    let els = pick(s => s === want);
    if (!els.length) els = pick(s => s.includes(want));
    return {els, by: 'text'};
  };
  const one = (kind) => {
    const f = find(a.target, kind);
    if (f.bad) return {ok: false, msg: 'Not a valid CSS selector: ' + f.bad};
    const els = f.els;
    if (!els.length) return {ok: false, wait: true, msg: 'Nothing on the page matches ' + JSON.stringify(a.target) + '.'};
    const n = Math.max(1, a.nth || 1);
    if (els.length < n) return {ok: false, wait: true, msg: 'Only ' + els.length + ' match ' + JSON.stringify(a.target) + '.'};
    return {ok: true, el: els[n - 1], count: els.length};
  };
  const point = el => {
    el.scrollIntoView({block: 'center', inline: 'center'});
    let target = el;
    if (!shown(el) && el.labels && el.labels.length) target = [...el.labels].find(shown) || el;
    const r = target.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const hit = document.elementFromPoint(x, y);
    if (hit && hit !== target && !target.contains(hit) && !(hit.control === el) && !hit.contains(target)) {
      return {ok: false, wait: true, msg: describe(el) + ' is covered by ' + describe(hit) + '.'};
    }
    return {ok: true, x, y, what: describe(el)};
  };
  const blocked = el => el.disabled || el.closest('[disabled], [aria-disabled=true]');
  const setValue = (el, v) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true}));
  };

  if (op === 'point') {
    const r = one(a.kind || 'click'); if (!r.ok) return r;
    if (a.kind !== 'hover' && blocked(r.el)) return {ok: false, wait: true, msg: describe(r.el) + ' is disabled.'};
    if (a.want_checked != null && !!r.el.checked === a.want_checked) return {ok: true, skip: true, what: describe(r.el)};
    return point(r.el);
  }
  if (op === 'focus') {
    const r = one('field'); if (!r.ok) return r;
    const el = r.el;
    if (blocked(el) || el.readOnly) return {ok: false, wait: true, msg: describe(el) + ' cannot be typed into (disabled or read-only).'};
    const p = point(el); if (!p.ok) return p;
    el.focus();
    if (a.clear) {
      if (el.isContentEditable) { document.getSelection().selectAllChildren(el); document.execCommand('delete'); }
      else if ('value' in el) setValue(el, '');
    }
    return {ok: true, what: describe(el)};
  }
  if (op === 'changed') {
    const el = document.activeElement;
    if (el && el !== document.body) el.dispatchEvent(new Event('change', {bubbles: true}));
    return {ok: true, value: el && 'value' in el ? String(el.value) : null};
  }
  if (op === 'select') {
    const r = one('field'); if (!r.ok) return r;
    const el = r.el;
    if (!(el instanceof HTMLSelectElement)) return {ok: false, msg: describe(el) + ' is not a drop-down list; click it and then the option instead.'};
    if (blocked(el)) return {ok: false, wait: true, msg: describe(el) + ' is disabled.'};
    const want = norm(a.option);
    const opts = [...el.options];
    const o = opts.find(o => norm(o.label) === want || norm(o.value) === want)
      || opts.find(o => norm(o.label).includes(want));
    if (!o) return {ok: false, wait: true, msg: describe(el) + ' has no option ' + JSON.stringify(a.option)
      + '. Its options: ' + opts.map(o => o.label).slice(0, 15).join(', ')};
    el.focus(); setValue(el, o.value); el.dispatchEvent(new Event('change', {bubbles: true}));
    return {ok: true, what: describe(el)};
  }
  if (op === 'see') {
    const text = norm(document.body ? document.body.innerText : '');
    const has = text.includes(norm(a.text));
    if (has === !a.not) return {ok: true};
    return {ok: false, wait: true, msg: a.not ? 'The page still shows ' + JSON.stringify(a.text) + '.'
      : 'The page does not show ' + JSON.stringify(a.text) + '.'};
  }
  if (op === 'expect') {
    const f = find(a.target, a.field ? 'field' : 'any');
    if (f.bad) return {ok: false, msg: 'Not a valid CSS selector: ' + f.bad};
    const els = f.hidden ? [] : f.els;
    if (a.count != null) {
      if (els.length === a.count) return {ok: true};
      return {ok: false, wait: true, msg: JSON.stringify(a.target) + ' matches ' + els.length + ', expected ' + a.count + '.'};
    }
    if (a.gone) return els.length ? {ok: false, wait: true, msg: JSON.stringify(a.target) + ' is still on the page.'} : {ok: true};
    if (!els.length) return {ok: false, wait: true, msg: 'Nothing on the page matches ' + JSON.stringify(a.target) + '.'};
    const el = els[Math.max(1, a.nth || 1) - 1] || els[0];
    if (a.text != null && !norm(el.innerText || el.value).includes(norm(a.text))) {
      return {ok: false, wait: true, msg: describe(el) + ' does not contain ' + JSON.stringify(a.text) + '.'};
    }
    if (a.value != null && String(el.value) !== String(a.value)) {
      return {ok: false, wait: true, msg: describe(el) + ' has value ' + JSON.stringify(String(el.value ?? '')) + ', expected ' + JSON.stringify(String(a.value)) + '.'};
    }
    if (a.checked != null && !!el.checked !== a.checked) return {ok: false, wait: true, msg: describe(el) + (a.checked ? ' is not ticked.' : ' is ticked.')};
    if (a.enabled != null && !blocked(el) !== a.enabled) return {ok: false, wait: true, msg: describe(el) + (a.enabled ? ' is disabled.' : ' is enabled.')};
    return {ok: true};
  }
  if (op === 'where') return {ok: true, href: location.href};
  if (op === 'look') {
    const ctl = [...document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [role=button]')]
      .filter(shown).slice(0, 30).map(describe);
    return {ok: true, text: (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim().slice(0, 600), controls: ctl};
  }
  return {ok: false, msg: 'unknown op ' + op};
})
'''


class _StepFailed(Exception):
    pass


def describe(step: dict) -> str:
    action = next((k for k in ACTIONS if k in step), '')
    value = step.get(action)
    if action in ('fill', 'type'):
        return f'fill {json.dumps(value)} with {json.dumps(step.get("with", step.get("text", "")))}'
    if action == 'select':
        return f'select {json.dumps(step.get("option", ""))} in {json.dumps(value)}'
    if action == 'expect':
        extra = ', '.join(f'{k} {json.dumps(step[k])}' for k in ('count', 'text', 'value', 'checked', 'enabled', 'gone')
                          if k in step)
        return f'expect {json.dumps(value)}' + (f' ({extra})' if extra else '')
    if action == 'press' and step.get('on'):
        return f'press {value} in {json.dumps(step["on"])}'
    if action == 'reload':
        return 'reload the page'
    return f'{action.replace("_", " ")} {json.dumps(value)}' if action else 'unknown step'


def check_steps(steps) -> list[dict]:
    """Normalise and check a list of steps (a JSON string is accepted). Raises ValueError."""
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except ValueError as exc:
            raise ValueError(f'steps is not valid JSON: {exc}') from None
    if isinstance(steps, dict):
        steps = steps.get('steps', [steps])
    if not isinstance(steps, list) or not steps:
        raise ValueError('steps must be a non-empty list like [{"click": "Add"}, {"see": "Added"}].')
    if len(steps) > MAX_STEPS:
        raise ValueError(f'At most {MAX_STEPS} steps per test.')
    out: list[dict] = []
    for i, step in enumerate(steps, 1):
        if isinstance(step, str):
            raise ValueError(f'Step {i} is text ({step[:60]!r}); each step is an object like {{"click": "Add"}}.')
        if not isinstance(step, dict):
            raise ValueError(f'Step {i} must be an object.')
        found = [k for k in ACTIONS if k in step]
        if len(found) != 1:
            raise ValueError(f'Step {i} needs exactly one action out of: {", ".join(ACTIONS)}.')
        action = found[0]
        if action in ('fill', 'type') and not any(k in step for k in ('with', 'text')):
            raise ValueError(f'Step {i} ({action}) needs "with": the text to type.')
        if action == 'select' and 'option' not in step:
            raise ValueError(f'Step {i} (select) needs "option".')
        if action == 'expect' and not str(step['expect'] or '').strip():
            raise ValueError(f'Step {i} (expect) needs a target.')
        if action == 'press' and str(step['press']).lower() not in KEYS and len(str(step['press'])) != 1:
            raise ValueError(f'Step {i}: unknown key {step["press"]!r}. Use one character or one of '
                             + ', '.join(sorted({v[0] for v in KEYS.values()})) + '.')
        out.append(step)
    return out


class _Runner:
    def __init__(self, page: _Page, base: str) -> None:
        self.page, self.dt, self.base = page, page.dt, base
        self.shots: list[dict] = []

    # -- talking to the page ------------------------------------------------
    def js(self, op: str, args: dict) -> dict:
        expr = f'{_HELPER}({json.dumps(op)}, {json.dumps(args)})'
        try:
            res = self.page.call('Runtime.evaluate', {'expression': expr, 'returnByValue': True}, timeout=10)
        except PageCheckError as exc:  # mid-navigation: no page to ask yet
            return {'ok': False, 'wait': True, 'msg': f'The page was not ready ({exc}).'}
        if res.get('exceptionDetails'):
            d = res['exceptionDetails']
            return {'ok': False, 'wait': True,
                    'msg': _short((d.get('exception') or {}).get('description') or d.get('text') or 'error', 200)}
        return (res.get('result') or {}).get('value') or {'ok': False, 'wait': True, 'msg': 'No answer from the page.'}

    def until(self, op: str, args: dict, wait: float) -> dict:
        deadline = time.monotonic() + wait
        while True:
            out = self.js(op, args)
            if out.get('ok') or not out.get('wait') or time.monotonic() >= deadline:
                if not out.get('ok'):
                    raise _StepFailed(out.get('msg') or 'failed')
                return out
            self.dt.pump(0.2)

    def settle(self, start: int, wait: float) -> None:
        """After an action: let a page load finish and the requests it started end."""
        self.dt.pump(0.3)
        deadline = time.monotonic() + max(wait, 3.0)
        while time.monotonic() < deadline:
            events = self.dt.events[start:]
            started = sum(1 for e in events if e.get('method') == 'Page.frameStartedLoading')
            loaded = sum(1 for e in events if e.get('method') == 'Page.loadEventFired')
            busy: set[str] = set()
            for e in events:
                m, rid = e.get('method'), (e.get('params') or {}).get('requestId')
                if m == 'Network.requestWillBeSent':
                    busy.add(rid)
                elif m in ('Network.loadingFinished', 'Network.loadingFailed'):
                    busy.discard(rid)
            if (not started or loaded) and not busy:
                self.dt.pump(0.15)
                if len(self.dt.events) == len(events) + start:
                    return
                continue
            self.dt.pump(0.1)

    def mouse(self, x: float, y: float, click: bool = True) -> None:
        self.page.call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': y})
        if click:
            for kind in ('mousePressed', 'mouseReleased'):
                self.page.call('Input.dispatchMouseEvent', {'type': kind, 'x': x, 'y': y, 'button': 'left',
                                                           'buttons': 1 if kind == 'mousePressed' else 0,
                                                           'clickCount': 1})

    def key(self, name: str) -> None:
        spec = KEYS.get(name.lower())
        if spec is None:  # one character
            self.page.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': name, 'text': name})
            self.page.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': name})
            return
        key, code, vk, text = spec
        down = {'type': 'keyDown', 'key': key, 'code': code, 'windowsVirtualKeyCode': vk, 'nativeVirtualKeyCode': vk}
        if text:
            down['text'] = down['unmodifiedText'] = text
        else:
            down['type'] = 'rawKeyDown'
        self.page.call('Input.dispatchKeyEvent', down)
        self.page.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': key, 'code': code,
                                                  'windowsVirtualKeyCode': vk, 'nativeVirtualKeyCode': vk})

    def navigate(self, url: str, wait: float) -> None:
        start = len(self.dt.events)
        nav = self.page.call('Page.navigate', {'url': url})
        if nav.get('errorText'):
            raise _StepFailed(f"The page didn't load: {nav['errorText']}")
        self._wait_load(start, wait)

    def _wait_load(self, start: int, wait: float) -> None:
        deadline = time.monotonic() + max(wait, 15.0)
        while time.monotonic() < deadline:
            if any(e.get('method') == 'Page.loadEventFired' for e in self.dt.events[start:]):
                break
            self.dt.pump(0.2)
        else:
            raise _StepFailed('The page did not finish loading.')
        self.settle(len(self.dt.events), wait)

    def path(self) -> str:
        href = str(self.js('where', {}).get('href') or '')
        split = urllib.parse.urlsplit(href)
        rest = split.path
        if rest.startswith(PREFIX):
            rest = rest[len(PREFIX):]
        base_path = urllib.parse.urlsplit(self.base).path
        if base_path != PREFIX + '/' and rest.startswith(base_path):
            rest = '/' + rest[len(base_path):]
        return (rest or '/') + (f'?{split.query}' if split.query else '') + (f'#{split.fragment}' if split.fragment else '')

    # -- one step ---------------------------------------------------------------
    def step(self, step: dict) -> str:
        """Do one step; returns a short note (or '') and raises _StepFailed."""
        action = next(k for k in ACTIONS if k in step)
        value = step[action]
        try:
            wait = min(MAX_WAIT, max(0.0, float(step.get('timeout') or DEFAULT_WAIT)))
        except (TypeError, ValueError):
            wait = DEFAULT_WAIT
        nth = int(step.get('nth') or 1) if str(step.get('nth') or '1').isdigit() else 1
        start = len(self.dt.events)
        if action == 'go':
            self.navigate(urllib.parse.urljoin(self.base, str(value or '').lstrip('/')), wait)
        elif action == 'reload':
            self.page.call('Page.reload', {})
            self._wait_load(start, wait)
        elif action in ('click', 'hover', 'check', 'uncheck'):
            args = {'target': value, 'nth': nth, 'kind': 'hover' if action == 'hover' else
                    ('field' if action in ('check', 'uncheck') else 'click')}
            if action in ('check', 'uncheck'):
                args['want_checked'] = action == 'check'
            at = self.until('point', args, wait)
            if at.get('skip'):
                return f'{at.get("what")} was already {"ticked" if action == "check" else "unticked"}'
            self.mouse(at['x'], at['y'], click=action != 'hover')
            self.settle(start, wait)
            return f'on {at.get("what")}'
        elif action in ('fill', 'type'):
            text = str(step.get('with', step.get('text', '')))
            at = self.until('focus', {'target': value, 'nth': nth, 'clear': True}, wait)
            if text:
                self.page.call('Input.insertText', {'text': text})
            got = self.js('changed', {})
            self.settle(start, wait)
            if got.get('value') is not None and got['value'] != text:
                return f'{at.get("what")} now holds {json.dumps(_short(got["value"], 80))}'
            return f'into {at.get("what")}'
        elif action == 'select':
            at = self.until('select', {'target': value, 'nth': nth, 'option': str(step.get('option'))}, wait)
            self.settle(start, wait)
            return f'in {at.get("what")}'
        elif action == 'press':
            if step.get('on'):
                self.until('focus', {'target': step['on'], 'nth': nth, 'clear': False}, wait)
            self.key(str(value))
            self.settle(start, wait)
        elif action in ('see', 'not_see'):
            self.until('see', {'text': str(value), 'not': action == 'not_see'}, wait)
        elif action == 'expect':
            args = {'target': value, 'nth': nth}
            for k in ('count', 'text', 'value', 'checked', 'enabled', 'gone'):
                if k in step:
                    args[k] = step[k]
            if 'count' in args:
                args['count'] = int(args['count'])
            # A value or a tick belongs to a field: "Title" means the field labelled Title.
            args['field'] = 'value' in args or 'checked' in args
            self.until('expect', args, wait)
        elif action == 'url':
            deadline = time.monotonic() + wait
            want = str(value or '')
            while True:
                here = self.path()
                if want in here or want.lstrip('/') in here:
                    break
                if time.monotonic() >= deadline:
                    raise _StepFailed(f'The address is {here}, which does not contain {json.dumps(want)}.')
                self.dt.pump(0.2)
            return here
        elif action == 'wait':
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.dt.pump(min(MAX_WAIT, max(0.0, float(value) / 1000)))
            else:
                self.until('expect', {'target': value, 'nth': nth}, max(wait, 10.0))
        elif action == 'js':
            try:
                res = self.page.call('Runtime.evaluate', {'expression': str(value), 'returnByValue': True,
                                                          'awaitPromise': True}, timeout=max(wait, 5))
            except PageCheckError as exc:
                raise _StepFailed(str(exc)) from None
            if res.get('exceptionDetails'):
                d = res['exceptionDetails']
                raise _StepFailed(_short((d.get('exception') or {}).get('description') or d.get('text') or 'error', 200))
            got = (res.get('result') or {}).get('value')
            if not got:
                raise _StepFailed(f'It came out {json.dumps(got)}, not true.')
            self.settle(start, 0)
        elif action == 'screenshot':
            if len(self.shots) < MAX_SHOTS:
                self.shots.append({'label': _short(str(value or f'screenshot {len(self.shots) + 1}'), 60),
                                   'data': self.page.screenshot()})
        return ''


def run_steps(url: str, steps: list[dict], *, browser: str, phone: bool = False,
              headers: dict[str, str] | None = None, no_sandbox: bool = False, timeout: float = 30) -> dict:
    """Open ``url`` and play ``steps``. Returns ``{ok, passed, steps: [...], screenshot,
    shots, errors, console, failed, page_text?, controls?}``; ``ok`` is false only when the
    browser could not be used at all."""
    steps = check_steps(steps)
    results: list[dict] = []
    began = time.monotonic()
    try:
        with _Page(browser, phone=phone, headers=headers, timeout=timeout, no_sandbox=no_sandbox) as page:
            run = _Runner(page, url)
            out: dict = {'ok': True, 'width': page.width, 'height': page.height}
            try:
                run.navigate(url, DEFAULT_WAIT)
                opened = None
            except _StepFailed as exc:
                opened = str(exc)
            failed_at = 0
            if opened:
                results.append({'n': 0, 'do': 'open the app', 'ok': False, 'error': opened})
                failed_at = -1
            for n, step in enumerate(steps if not opened else [], 1):
                start = len(page.dt.events)
                answer = step.get('dialog', 'accept')
                page.dt.dialog_answer = (True if answer in (None, True, 'accept', 'ok', 'yes')
                                         else False if answer in (False, 'dismiss', 'cancel', 'no') else str(answer))
                t0 = time.monotonic()
                row: dict = {'n': n, 'do': describe(step)}
                try:
                    if time.monotonic() - began > BUDGET:
                        raise _StepFailed(f'The test ran over {int(BUDGET)} seconds; split it into smaller tests.')
                    note = run.step(step)
                    row['ok'] = True
                    if note:
                        row['note'] = note
                except _StepFailed as exc:
                    row.update(ok=False, error=str(exc))
                except PageCheckError as exc:
                    row.update(ok=False, error=f'The browser stopped answering: {exc}')
                events = page.dt.events[start:]
                found = _summarise(events, url)
                for key in ('errors', 'console', 'failed'):
                    if found[key]:
                        row[key] = found[key]
                dialogs = [_short((e.get('params') or {}).get('message', ''), 120) for e in events
                           if e.get('method') == 'Page.javascriptDialogOpening']
                if dialogs:
                    row['dialogs'] = dialogs
                row['ms'] = int((time.monotonic() - t0) * 1000)
                results.append(row)
                if not row['ok']:
                    failed_at = n
                    break
            whole = _summarise(page.dt.events, url)
            out.update({k: whole[k] for k in ('errors', 'console', 'failed')})
            if failed_at:
                try:
                    look = run.js('look', {})
                    out['page_text'] = look.get('text', '')
                    out['controls'] = look.get('controls', [])
                    out['address'] = run.path()
                except PageCheckError:
                    pass
            try:
                out['screenshot'] = page.screenshot()
            except PageCheckError:
                out['screenshot'] = ''
            out['shots'] = run.shots
            out['steps'] = results
            out['passed'] = not failed_at and not whole['errors'] and len(results) == len(steps)
            out['total'] = len(steps)
            return out
    except (OSError, ValueError) as exc:
        raise PageCheckError(f'Could not drive the browser: {exc}') from None


def test_app_port(port: int, steps, path: str = '', *, phone: bool = False,
                  headers: dict[str, str] | None = None, no_sandbox: bool = False) -> dict:
    """Play ``steps`` against the app on ``port`` the way people get it (under a
    path prefix, in an opaque origin), like :func:`pagecheck.check_app_port`."""
    try:
        steps = check_steps(steps)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc), 'bad_steps': True}
    browser = find_browser()
    if browser is None:
        return {'ok': False, 'no_browser': True,
                'error': 'No Edge, Chrome or Chromium was found where the app runs, so it cannot be clicked '
                         'through. Use Check App for a text check, or set COGNITRIX_CHECK_BROWSER.'}
    hosting = _Hosting(int(port))
    try:
        out = run_steps(hosting.url + str(path or '').lstrip('/'), steps, browser=browser, phone=phone,
                        headers=headers, no_sandbox=no_sandbox)
    except PageCheckError as exc:
        return {'ok': False, 'error': f'The browser could not run the test: {exc}'}
    finally:
        hosting.close()

    def tidy(items):
        cleaned = []
        for item in items or []:
            where = item.split(' ')[-1]
            if where.startswith('/') and not where.startswith(PREFIX):
                item += ' (the link starts with "/": make it relative)'
            cleaned.append(item.replace(PREFIX + '/', '/').replace(PREFIX, '/'))
        return cleaned

    for row in [out] + list(out.get('steps') or []):
        if row.get('failed'):
            row['failed'] = tidy(row['failed'])
    return out


def test_app_copy(host, slug: str, steps, path: str = '', *, data: str = 'fresh', phone: bool = False,
                  headers: dict[str, str] | None = None, no_sandbox: bool = False) -> dict:
    """Run a test against a throwaway copy of ``slug``'s live version on ``host``
    (an ``apps.AppHost``), so the clicks never change the data people see."""
    try:
        steps = check_steps(steps)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc), 'bad_steps': True}
    if find_browser() is None:
        return test_app_port(0, steps)  # the "no browser" answer, without starting anything
    with host.scratch(slug, 'copy' if data == 'copy' else 'fresh') as copy:
        out = test_app_port(copy.port, steps, path, phone=phone, headers=headers, no_sandbox=no_sandbox)
        out['version'] = copy.version
        if not out.get('passed'):
            out['log'] = copy.log(40)
    return out
