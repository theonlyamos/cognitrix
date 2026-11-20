"""Diffs for the changes view: hunks, and how to put one back.

A hunk is ``{'id', 'old_start', 'new_start', 'lines': [[op, text], ...]}`` with
``op`` one of ' ', '-', '+'. Its id comes from the path and the changed lines
(not their position), so the person's "Accept" stays on a hunk while other
hunks of the file are put back.
"""
from __future__ import annotations

import difflib
import hashlib
import re

CONTEXT = 3
MAX_HUNKS_PER_FILE = 200


def hunk_id(path: str, lines: list[list[str]]) -> str:
    changed = '\n'.join(f'{op}{text}' for op, text in lines if op != ' ')
    return hashlib.sha1(f'{path}\0{changed}'.encode('utf-8', 'replace'), usedforsecurity=False).hexdigest()[:16]


def split_lines(text: str | None) -> list[str]:
    """Lines of a text as the file tools show it (no line ends)."""
    if not text:
        return []
    return text.replace('\r\n', '\n').split('\n')


def hunks_from_texts(path: str, before: str | None, after: str | None, context: int = CONTEXT) -> list[dict]:
    a, b = split_lines(before), split_lines(after)
    out: list[dict] = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for group in matcher.get_grouped_opcodes(context):
        lines: list[list[str]] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == 'equal':
                lines += [[' ', t] for t in a[i1:i2]]
                continue
            if tag in ('replace', 'delete'):
                lines += [['-', t] for t in a[i1:i2]]
            if tag in ('replace', 'insert'):
                lines += [['+', t] for t in b[j1:j2]]
        first = group[0]
        out.append({'id': hunk_id(path, lines), 'old_start': first[1] + 1, 'new_start': first[3] + 1, 'lines': lines})
        if len(out) >= MAX_HUNKS_PER_FILE:
            break
    return out


_FILE_HEAD = re.compile(r'^diff --git a/(.*) b/(.*)$')
_HUNK_HEAD = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def parse_git_patch(patch: str) -> dict[str, list[dict]]:
    """Hunks per path from ``git diff`` output (paths relative to the project).
    A hunk ends when its header's line counts are used up."""
    files: dict[str, list[dict]] = {}
    path = None
    hunk: dict | None = None
    old_left = new_left = 0
    for raw in (patch or '').split('\n'):
        m = _FILE_HEAD.match(raw)
        if m:
            path, hunk = m.group(2), None
            files.setdefault(path, [])
            continue
        if path is None:
            continue
        h = _HUNK_HEAD.match(raw)
        if h and (hunk is None or (old_left <= 0 and new_left <= 0)):
            old_left = int(h.group(2)) if h.group(2) is not None else 1
            new_left = int(h.group(4)) if h.group(4) is not None else 1
            hunk = {'id': '', 'old_start': int(h.group(1)), 'new_start': int(h.group(3)), 'lines': []}
            files[path].append(hunk)
            continue
        if hunk is None or (old_left <= 0 and new_left <= 0):
            continue
        op, text = (raw[0], raw[1:]) if raw else (' ', '')
        if op == '\\':
            continue  # "\ No newline at end of file"
        if op == ' ':
            old_left -= 1
            new_left -= 1
        elif op == '-':
            old_left -= 1
        elif op == '+':
            new_left -= 1
        else:
            continue
        hunk['lines'].append([op, text])
    for p, hunks in files.items():
        for h in hunks:
            h['id'] = hunk_id(p, h['lines'])
    return files


def sides(hunk: dict) -> tuple[list[str], list[str]]:
    """(old lines, new lines) of a hunk, context included."""
    old = [t for op, t in hunk['lines'] if op in (' ', '-')]
    new = [t for op, t in hunk['lines'] if op in (' ', '+')]
    return old, new


def revert_edit(hunk: dict) -> tuple[str, str]:
    """``(find, replace)`` that puts this hunk back: the new side becomes the
    old side again. Raises ValueError when the hunk can't be found that way
    (it has no lines on the new side)."""
    old, new = sides(hunk)
    if not new:
        raise ValueError('nothing to find')
    return '\n'.join(new), '\n'.join(old)


def apply_revert(text: str, hunk: dict) -> str:
    """``text`` with ``hunk`` put back (what the file tools' Edit would do).
    Raises ValueError when the hunk's lines aren't there exactly once."""
    find, replace = revert_edit(hunk)
    count = text.count(find)
    if count == 0:
        raise ValueError('changed')
    if count > 1:
        raise ValueError('ambiguous')
    return text.replace(find, replace, 1)


def counts(hunks: list[dict]) -> tuple[int, int]:
    added = sum(1 for h in hunks for op, _ in h['lines'] if op == '+')
    removed = sum(1 for h in hunks for op, _ in h['lines'] if op == '-')
    return added, removed


def summary(hunks: list[dict], accepted: set[str] | list[str] | None) -> str:
    accepted = set(accepted or ())
    ids = [h['id'] for h in hunks]
    if ids and all(i in accepted for i in ids):
        return 'accepted'
    return 'pending'

