"""Sheets and whiteboards: artifacts people and agents edit as data.

An HTML or Markdown artifact is a page an agent writes; people can only look
at it and comment. A ``sheet`` or ``whiteboard`` artifact is stored as JSON
that both sides change: the agent publishes and reads it with the artifact
tools, and people edit it in the app (``frontend/src/components/boards``),
each save a new version (see ``store.save_edit``).

Sheet (``format: "sheet"``)::

    {"tabs": [{"name": "Budget",
               "cells": {"A1": "Item", "B1": "Cost", "B2": 1200, "B4": "=SUM(B2:B3)"},
               "styles": {"A1": {"bold": true}, "B4": {"fmt": "currency"}},
               "widths": {"A": 180}}]}

Agents may also send ``rows`` (a list of lists) instead of ``cells``, a single
tab without ``tabs``, a bare list of rows, or CSV text. Formulas start with
``=`` and support + - * / ^ &, comparisons, cell and range references (also
``Tab!A1`` and ``'My tab'!A1:B4``) and SUM, AVERAGE, MIN, MAX, COUNT, COUNTA,
IF, ROUND, ABS, CONCAT, LEN, UPPER, LOWER, AND, OR, NOT. The same rules are
in ``frontend/src/lib/sheetFormulas.ts``; keep the two in step.

Whiteboard (``format: "whiteboard"``)::

    {"items": [{"id": "n1", "type": "note", "x": 40, "y": 40, "w": 180, "h": 120,
                "text": "Goals", "color": "yellow"},
               {"id": "a1", "type": "arrow", "from": "n1", "to": "n2"}]}

Types: note, rect, ellipse, text, arrow, line, draw (``points``). Items
without a position are laid out in a grid.
"""
from __future__ import annotations

import csv
import html
import io
import json
import math
import re
import uuid
from typing import Any

SHEET = 'sheet'
WHITEBOARD = 'whiteboard'
DATA_FORMATS = (SHEET, WHITEBOARD)

MAX_TABS = 20
MAX_ROWS = 5000
MAX_COLS = 100
MAX_CELLS = 50_000
MAX_CELL_TEXT = 5000
MAX_ITEMS = 2000
MAX_TEXT = 4000
MAX_POINTS = 2000
COORD_LIMIT = 100_000

FORMATS_OF_CELL = ('number', 'currency', 'percent', 'text')
COLORS = ('yellow', 'green', 'blue', 'pink', 'purple', 'orange', 'gray', 'white')
ITEM_TYPES = ('note', 'rect', 'ellipse', 'text', 'arrow', 'line', 'draw')


class DocumentError(ValueError):
    pass


# ------------------------------------------------------------------ cells and names

_REF = re.compile(r'^([A-Z]{1,3})([1-9][0-9]{0,5})$')


def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def col_name(index: int) -> str:
    out = ''
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        out = chr(65 + rest) + out
    return out


def split_ref(ref: str) -> tuple[int, int] | None:
    """'B3' -> (row 2, col 1), zero-based. None when it isn't a cell in range."""
    match = _REF.match(ref.replace('$', '').upper())
    if not match:
        return None
    col, row = col_index(match.group(1)), int(match.group(2)) - 1
    if col >= MAX_COLS or row >= MAX_ROWS:
        return None
    return row, col


def _cell_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    text = str(value)
    if len(text) > MAX_CELL_TEXT:
        text = text[:MAX_CELL_TEXT]
    return text if text != '' else None


def _parse_scalar(text: str) -> Any:
    """CSV text cells: numbers become numbers."""
    stripped = text.strip()
    if re.fullmatch(r'-?\d+', stripped) and len(stripped) < 16:
        return int(stripped)
    if re.fullmatch(r'-?\d*\.\d+', stripped):
        return float(stripped)
    return text


_CELL_KEYS = ('value', 'v', 'formula', 'text')


def _split_cell(value: Any, ref: str, styles: dict[str, dict[str, Any]]) -> Any:
    """A cell written as an object ({"value": 640, "bold": true, "format": "currency"}):
    the value stays in the cell and the rest becomes the cell's style."""
    if not isinstance(value, dict):
        return value
    styles[ref] = {k: v for k, v in value.items() if k not in _CELL_KEYS}
    return next((value[k] for k in _CELL_KEYS if k in value), None)


def _rows_to_cells(rows: Any, styles: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if not isinstance(rows, list):
        raise DocumentError('rows must be a list of lists')
    cells: dict[str, Any] = {}
    styles = {} if styles is None else styles
    for r, row in enumerate(rows[:MAX_ROWS]):
        if not isinstance(row, list):
            row = [row]
        for c, value in enumerate(row[:MAX_COLS]):
            ref = f'{col_name(c)}{r + 1}'
            value = _cell_value(_split_cell(value, ref, styles))
            if value is not None:
                cells[ref] = value
    return cells


def _clean_tab(raw: Any, index: int) -> dict[str, Any]:
    if isinstance(raw, list):
        raw = {'rows': raw}
    if not isinstance(raw, dict):
        raise DocumentError(f'tab {index + 1} must be an object with "cells" or "rows"')
    name = ' '.join(str(raw.get('name') or f'Sheet{index + 1}').split())[:60] or f'Sheet{index + 1}'
    cells: dict[str, Any] = {}
    cell_styles: dict[str, dict[str, Any]] = {}
    if 'rows' in raw and raw.get('rows') is not None:
        cells.update(_rows_to_cells(raw.get('rows'), cell_styles))
    given = raw.get('cells')
    if isinstance(given, dict):
        for ref, value in given.items():
            spot = split_ref(str(ref))
            if spot is None:
                raise DocumentError(f'"{ref}" is not a cell like A1 (columns up to {col_name(MAX_COLS - 1)}, '
                                    f'rows up to {MAX_ROWS})')
            key = f'{col_name(spot[1])}{spot[0] + 1}'
            value = _cell_value(_split_cell(value, key, cell_styles))
            if value is None:
                cells.pop(key, None)
            else:
                cells[key] = value
    styles: dict[str, dict[str, Any]] = {}
    given_styles = dict(raw.get('styles') or {}) if isinstance(raw.get('styles'), dict) else {}
    given_styles.update(cell_styles)
    for ref, style in given_styles.items():
        spot = split_ref(str(ref))
        if spot is None or not isinstance(style, dict):
            continue
        clean: dict[str, Any] = {}
        if style.get('bold'):
            clean['bold'] = True
        fmt = style.get('fmt', style.get('format'))
        fmt = {'money': 'currency', '%': 'percent', 'percentage': 'percent'}.get(fmt, fmt)
        if fmt in FORMATS_OF_CELL:
            clean['fmt'] = fmt
        if style.get('align') in ('left', 'center', 'right'):
            clean['align'] = style['align']
        if clean:
            styles[f'{col_name(spot[1])}{spot[0] + 1}'] = clean
    widths: dict[str, int] = {}
    for col, width in (raw.get('widths') or {}).items() if isinstance(raw.get('widths'), dict) else ():
        letters = str(col).upper()
        if re.fullmatch(r'[A-Z]{1,3}', letters) and col_index(letters) < MAX_COLS:
            try:
                widths[letters] = max(40, min(600, int(width)))
            except (TypeError, ValueError):
                continue
    tab: dict[str, Any] = {'name': name, 'cells': cells}
    if styles:
        tab['styles'] = styles
    if widths:
        tab['widths'] = widths
    return tab


def normalize_sheet(text: str) -> str:
    """Any sheet input an agent or the app sends, as canonical JSON text."""
    data: Any
    try:
        data = json.loads(text)
    except ValueError:
        rows = list(csv.reader(io.StringIO(text)))
        data = {'tabs': [{'name': 'Sheet1', 'rows': [[_parse_scalar(v) for v in row] for row in rows]}]}
    if isinstance(data, list):
        data = {'tabs': [{'name': 'Sheet1', 'rows': data}]}
    if not isinstance(data, dict):
        raise DocumentError('A sheet is JSON like {"tabs": [{"name": "Sheet1", "cells": {"A1": "Item"}}]}')
    tabs = data.get('tabs')
    if tabs is None:
        tabs = [data]
    if not isinstance(tabs, list) or not tabs:
        raise DocumentError('"tabs" must be a list with at least one tab')
    if len(tabs) > MAX_TABS:
        raise DocumentError(f'A sheet has at most {MAX_TABS} tabs')
    clean = [_clean_tab(tab, i) for i, tab in enumerate(tabs)]
    names: set[str] = set()
    for i, tab in enumerate(clean):
        base, n = tab['name'], 2
        while tab['name'].lower() in names:
            tab['name'] = f'{base} {n}'
            n += 1
        names.add(tab['name'].lower())
        clean[i] = tab
    if sum(len(t['cells']) for t in clean) > MAX_CELLS:
        raise DocumentError(f'A sheet holds at most {MAX_CELLS} filled cells')
    return json.dumps({'tabs': clean}, ensure_ascii=False, separators=(',', ':'))


# ------------------------------------------------------------------ formulas

class _Err(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_TOKEN = re.compile(r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?|\.\d+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_]*)!)?\$?[A-Za-z]{1,3}\$?[0-9]{1,6}(?::\$?[A-Za-z]{1,3}\$?[0-9]{1,6})?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<op><>|<=|>=|[-+*/^&=<>(),:%])
)""", re.X)


def _tokens(src: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    src = src.rstrip()
    while pos < len(src):
        match = _TOKEN.match(src, pos)
        if not match or match.end() == pos:
            raise _Err('#ERROR!')
        pos = match.end()
        kind = match.lastgroup or ''
        out.append((kind, match.group(kind)))
    return out


def _num(value: Any) -> float:
    if value is None or value == '':
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        raise _Err('#VALUE!')


def _text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        if value.upper() in ('TRUE', 'FALSE'):
            return value.upper() == 'TRUE'
        return _num(value) != 0
    return bool(value)


def _flat(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for value in values:
        out.extend(value if isinstance(value, list) else [value])
    return out


def _numbers(values: list[Any]) -> list[float]:
    out = []
    for value in _flat(values):
        if isinstance(value, bool) or value is None or value == '':
            continue
        if isinstance(value, int | float):
            out.append(float(value))
        else:
            try:
                out.append(float(str(value)))
            except ValueError:
                continue
    return out


def _round(x: float, digits: float = 0) -> float:
    q = 10 ** int(digits)
    return math.floor(abs(x) * q + 0.5) / q * (1 if x >= 0 else -1)


_FUNCS = {
    'SUM': lambda a: sum(_numbers(a)),
    'AVERAGE': lambda a: (sum(_numbers(a)) / len(_numbers(a))) if _numbers(a) else (_ for _ in ()).throw(_Err('#DIV/0!')),
    'MIN': lambda a: min(_numbers(a)) if _numbers(a) else 0.0,
    'MAX': lambda a: max(_numbers(a)) if _numbers(a) else 0.0,
    'COUNT': lambda a: float(len(_numbers(a))),
    'COUNTA': lambda a: float(len([v for v in _flat(a) if v not in (None, '')])),
    'ABS': lambda a: abs(_num(a[0])) if a else 0.0,
    'ROUND': lambda a: _round(_num(a[0]), _num(a[1]) if len(a) > 1 else 0) if a else 0.0,
    'CONCAT': lambda a: ''.join(_text(v) for v in _flat(a)),
    'LEN': lambda a: float(len(_text(a[0]))) if a else 0.0,
    'UPPER': lambda a: _text(a[0]).upper() if a else '',
    'LOWER': lambda a: _text(a[0]).lower() if a else '',
    'AND': lambda a: all(_truthy(v) for v in _flat(a)),
    'OR': lambda a: any(_truthy(v) for v in _flat(a)),
    'NOT': lambda a: not _truthy(a[0]) if a else True,
}


class Sheet:
    """Computed values of a sheet's cells (formulas evaluated, with errors as #CODE!)."""

    def __init__(self, data: dict[str, Any]):
        self.tabs = {t['name'].lower(): t for t in data.get('tabs', [])}
        self.first = data['tabs'][0]['name'].lower() if data.get('tabs') else ''
        self.cache: dict[tuple[str, str], Any] = {}
        self.busy: set[tuple[str, str]] = set()

    def value(self, tab: str, ref: str) -> Any:
        key = (tab.lower(), ref)
        if key in self.cache:
            return self.cache[key]
        sheet = self.tabs.get(tab.lower())
        if sheet is None:
            raise _Err('#REF!')
        raw = sheet['cells'].get(ref)
        if not (isinstance(raw, str) and raw.startswith('=')):
            return raw
        if key in self.busy:
            raise _Err('#CYCLE!')
        self.busy.add(key)
        try:
            result = self._evaluate(tab.lower(), raw[1:])
        except _Err as exc:
            result = exc.code
        except ZeroDivisionError:
            result = '#DIV/0!'
        except (OverflowError, ValueError, TypeError, RecursionError, IndexError):
            result = '#VALUE!'
        finally:
            self.busy.discard(key)
        if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
            result = int(result)
        self.cache[key] = result
        return result

    def shown(self, tab: str, ref: str) -> Any:
        try:
            return self.value(tab, ref)
        except _Err as exc:
            return exc.code

    # A small Pratt parser over the tokens.
    def _evaluate(self, tab: str, src: str) -> Any:
        tokens = _tokens(src)
        self._toks, self._at, self._tab = tokens, 0, tab
        value = self._expr(1)
        if self._at != len(self._toks):
            raise _Err('#ERROR!')
        if isinstance(value, list):
            raise _Err('#VALUE!')
        return value

    def _peek(self) -> tuple[str, str] | None:
        return self._toks[self._at] if self._at < len(self._toks) else None

    def _take(self) -> tuple[str, str]:
        tok = self._peek()
        if tok is None:
            raise _Err('#ERROR!')
        self._at += 1
        return tok

    _BINARY = {'=': 1, '<>': 1, '<': 1, '>': 1, '<=': 1, '>=': 1, '&': 2, '+': 3, '-': 3, '*': 4, '/': 4, '^': 5}

    def _expr(self, min_power: int) -> Any:
        """Precedence climbing; every operator is left-associative except ^."""
        left = self._unary()
        while True:
            tok = self._peek()
            if not tok or tok[0] != 'op' or tok[1] not in self._BINARY:
                return left
            power = self._BINARY[tok[1]]
            if power < min_power:
                return left
            self._take()
            right = self._expr(power if tok[1] == '^' else power + 1)
            left = self._apply(tok[1], left, right)

    def _apply(self, op: str, a: Any, b: Any) -> Any:
        if isinstance(a, list) or isinstance(b, list):
            raise _Err('#VALUE!')
        if op == '&':
            return _text(a) + _text(b)
        if op in ('=', '<>', '<', '>', '<=', '>='):
            if isinstance(a, str) or isinstance(b, str):
                x, y = _text(a).lower(), _text(b).lower()
            else:
                x, y = _num(a), _num(b)
            return {'=': x == y, '<>': x != y, '<': x < y, '>': x > y, '<=': x <= y, '>=': x >= y}[op]
        x, y = _num(a), _num(b)
        if op == '+':
            return x + y
        if op == '-':
            return x - y
        if op == '*':
            return x * y
        if op == '/':
            if y == 0:
                raise _Err('#DIV/0!')
            return x / y
        if op == '^':
            result = x ** y
            if isinstance(result, complex) or not math.isfinite(result):
                raise _Err('#VALUE!')
            return result
        raise _Err('#ERROR!')

    def _unary(self) -> Any:
        tok = self._peek()
        if tok and tok == ('op', '-'):
            self._take()
            return -_num(self._unary())
        if tok and tok == ('op', '+'):
            self._take()
            return _num(self._unary())
        value = self._atom()
        nxt = self._peek()
        if nxt == ('op', '%'):
            self._take()
            return _num(value) / 100
        return value

    def _atom(self) -> Any:
        kind, text = self._take()
        if kind == 'num':
            return float(text)
        if kind == 'str':
            return text[1:-1].replace('""', '"')
        if kind == 'ref':
            return self._ref(text)
        if kind == 'name':
            upper = text.upper()
            if upper in ('TRUE', 'FALSE'):
                return upper == 'TRUE'
            if self._peek() != ('op', '('):
                raise _Err('#NAME?')
            self._take()
            args: list[Any] = []
            if self._peek() != ('op', ')'):
                while True:
                    args.append(self._expr(1))
                    if self._peek() == ('op', ','):
                        self._take()
                        continue
                    break
            if self._take() != ('op', ')'):
                raise _Err('#ERROR!')
            if upper == 'IF':
                if not args:
                    raise _Err('#VALUE!')
                if len(args) == 1:
                    return _truthy(args[0])
                return args[1] if _truthy(args[0]) else (args[2] if len(args) > 2 else False)
            func = _FUNCS.get(upper)
            if func is None:
                raise _Err('#NAME?')
            return func(args)
        if kind == 'op' and text == '(':
            value = self._expr(1)
            if self._take() != ('op', ')'):
                raise _Err('#ERROR!')
            return value
        raise _Err('#ERROR!')

    def _ref(self, text: str) -> Any:
        tab = self._tab
        if '!' in text:
            name, text = text.rsplit('!', 1)
            tab = (name[1:-1].replace("''", "'") if name.startswith("'") else name).lower()
            if tab not in self.tabs:
                raise _Err('#REF!')
        if ':' in text:
            start, end = (split_ref(p) for p in text.split(':', 1))
            if start is None or end is None:
                raise _Err('#REF!')
            (r1, c1), (r2, c2) = start, end
            r1, r2 = sorted((r1, r2))
            c1, c2 = sorted((c1, c2))
            if (r2 - r1 + 1) * (c2 - c1 + 1) > MAX_CELLS:
                raise _Err('#REF!')
            cells = self.tabs[tab]['cells']
            out = []
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    ref = f'{col_name(c)}{r + 1}'
                    if ref in cells:
                        value = self.value(tab, ref)
                        if isinstance(value, str) and value.startswith('#') and value.endswith(('!', '?')):
                            raise _Err(value)
                        out.append(value)
            return out
        spot = split_ref(text)
        if spot is None:
            raise _Err('#REF!')
        value = self.value(tab, f'{col_name(spot[1])}{spot[0] + 1}')
        if isinstance(value, str) and value.startswith('#') and value.endswith(('!', '?')):
            raise _Err(value)
        return value


def _bounds(cells: dict[str, Any]) -> tuple[int, int]:
    rows = cols = 0
    for ref in cells:
        spot = split_ref(ref)
        if spot:
            rows, cols = max(rows, spot[0] + 1), max(cols, spot[1] + 1)
    return rows, cols


def shown_text(value: Any, fmt: str | None = None) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, int | float) and fmt == 'percent':
        return f'{value * 100:.1f}%'.replace('.0%', '%')
    if isinstance(value, int | float) and fmt == 'currency':
        return f'{value:,.2f}'
    if isinstance(value, float):
        return f'{value:.10g}'
    return str(value)


def sheet_summary(source: str, limit: int = 60_000) -> str:
    """The sheet as an agent reads it: per tab, each filled cell with its
    formula (if any) and its computed value."""
    data = json.loads(source)
    sheet = Sheet(data)
    lines: list[str] = []
    for tab in data.get('tabs', []):
        rows, cols = _bounds(tab['cells'])
        lines.append(f'Tab "{tab["name"]}" ({rows} rows x {cols} columns):')
        styles = tab.get('styles') or {}
        for r in range(rows):
            parts = []
            for c in range(cols):
                ref = f'{col_name(c)}{r + 1}'
                raw = tab['cells'].get(ref)
                if raw is None:
                    continue
                value = sheet.shown(tab['name'], ref)
                style = styles.get(ref) or {}
                text = f'{ref}: {raw} -> {shown_text(value)}' if isinstance(raw, str) and raw.startswith('=') else f'{ref}: {shown_text(value)}'
                # How the person sees it on screen, when that differs.
                looks = [f'shown "{shown_text(value, style["fmt"])}"'] if style.get('fmt') and shown_text(value, style['fmt']) != shown_text(value) else []
                looks += [k for k in ('bold',) if style.get(k)]
                parts.append(text + (f' ({", ".join(looks)})' if looks else ''))
            if parts:
                lines.append('  ' + ' | '.join(parts))
        if sum(len(x) for x in lines) > limit:
            lines.append('(cut short)')
            break
    return '\n'.join(lines)


# ------------------------------------------------------------------ whiteboards

_HEX = re.compile(r'^#[0-9a-fA-F]{6}$')


def _coord(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(-COORD_LIMIT, min(COORD_LIMIT, round(number, 1)))


def _color(value: Any, default: str) -> str:
    text = str(value or '').strip().lower()
    if text in COLORS or _HEX.match(text):
        return text
    return default


_BOARD_LISTS = ('nodes', 'shapes', 'notes', 'elements', 'edges', 'connectors', 'connections', 'links', 'arrows')
_BOARD_LINKS = ('edges', 'connectors', 'connections', 'links', 'arrows')
_TYPE_ALIASES = {'sticky': 'note', 'sticky_note': 'note', 'stickynote': 'note', 'box': 'rect', 'rectangle': 'rect',
                 'square': 'rect', 'circle': 'ellipse', 'oval': 'ellipse', 'label': 'text', 'edge': 'arrow',
                 'connector': 'arrow', 'link': 'arrow', 'path': 'draw', 'pen': 'draw', 'freehand': 'draw'}


def _board_aliases(raw: Any) -> Any:
    """Common other names for the same things (label, source/target, shape...)."""
    if not isinstance(raw, dict):
        return raw
    item = dict(raw)
    for alias, key in (('label', 'text'), ('title', 'text'), ('content', 'text'), ('source', 'from'), ('target', 'to'),
                       ('width', 'w'), ('height', 'h'), ('kind', 'type'), ('shape', 'type')):
        if alias in item and key not in item:
            item[key] = item.pop(alias)
    kind = str(item.get('type') or '').lower().replace('-', '_')
    item['type'] = _TYPE_ALIASES.get(kind, kind or 'note')
    return item


def normalize_board(text: str) -> str:
    try:
        data = json.loads(text)
    except ValueError:
        raise DocumentError('A whiteboard is JSON like {"items": [{"type": "note", "text": "Idea"}]}')
    if isinstance(data, list):
        data = {'items': data}
    if isinstance(data, dict) and 'items' not in data:
        # Other shapes agents reach for: nodes + edges, shapes + connectors.
        found = [k for k in _BOARD_LISTS if isinstance(data.get(k), list)]
        if not found and data:
            raise DocumentError('A whiteboard is JSON like {"items": [{"id": "n1", "type": "note", "text": "Idea"}, '
                                '{"type": "arrow", "from": "n1", "to": "n2"}]}')
        items_in: list[Any] = []
        for key in found:
            for item in data[key]:
                if key in _BOARD_LINKS and isinstance(item, dict) and not item.get('type'):
                    item = {**item, 'type': 'arrow'}
                items_in.append(item)
        data = {'items': items_in}
    if not isinstance(data, dict) or not isinstance(data.get('items', []), list):
        raise DocumentError('A whiteboard is JSON like {"items": [{"type": "note", "text": "Idea"}]}')
    raw_items = [_board_aliases(i) for i in data.get('items') or []]
    if len(raw_items) > MAX_ITEMS:
        raise DocumentError(f'A whiteboard holds at most {MAX_ITEMS} items')
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    placed = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get('type') or 'note').lower()
        if kind not in ITEM_TYPES:
            kind = 'note'
        item_id = re.sub(r'[^A-Za-z0-9_-]', '', str(raw.get('id') or ''))[:40] or uuid.uuid4().hex[:10]
        while item_id in seen:
            item_id = f'{item_id}_{uuid.uuid4().hex[:4]}'
        seen.add(item_id)
        item: dict[str, Any] = {'id': item_id, 'type': kind}
        if kind in ('arrow', 'line', 'draw'):
            points = raw.get('points')
            if isinstance(points, list):
                clean = []
                for p in points[:MAX_POINTS]:
                    if isinstance(p, list | tuple) and len(p) >= 2:
                        clean.append([_coord(p[0], 0), _coord(p[1], 0)])
                if clean:
                    item['points'] = clean
            for end in ('from', 'to'):
                if raw.get(end):
                    item[end] = re.sub(r'[^A-Za-z0-9_-]', '', str(raw[end]))[:40]
            if kind == 'draw' and 'points' not in item:
                continue
            if kind != 'draw' and 'points' not in item and not (item.get('from') and item.get('to')):
                continue
            item['color'] = _color(raw.get('color'), 'gray')
        else:
            default_w, default_h = {'note': (180, 120), 'text': (220, 40)}.get(kind, (160, 100))
            if raw.get('x') is None or raw.get('y') is None:
                # Grid layout for items an agent sent without a place.
                col, row = placed % 4, placed // 4
                x, y = 40 + col * 220, 40 + row * 170
                placed += 1
            else:
                x, y = _coord(raw.get('x'), 0), _coord(raw.get('y'), 0)
            item.update({
                'x': x, 'y': y,
                'w': max(20, _coord(raw.get('w'), default_w)), 'h': max(20, _coord(raw.get('h'), default_h)),
                'color': _color(raw.get('color'), 'yellow' if kind == 'note' else 'gray' if kind == 'text' else 'blue'),
            })
        label = raw.get('text')
        if label is not None and str(label).strip():
            item['text'] = str(label)[:MAX_TEXT]
        items.append(item)
    known = {i['id'] for i in items}
    for item in items:
        for end in ('from', 'to'):
            if item.get(end) and item[end] not in known:
                item.pop(end)
    items = [i for i in items if i['type'] not in ('arrow', 'line') or 'points' in i or (i.get('from') and i.get('to'))]
    return json.dumps({'items': items}, ensure_ascii=False, separators=(',', ':'))


def board_summary(source: str, limit: int = 60_000) -> str:
    data = json.loads(source)
    names = {i['id']: (i.get('text') or i['type'])[:40] for i in data.get('items', [])}
    lines = [f'{len(data.get("items", []))} items:']
    for item in data.get('items', []):
        if item['type'] in ('arrow', 'line') and item.get('from'):
            lines.append(f'- {item["id"]} {item["type"]} from "{names.get(item["from"], "?")}" to "{names.get(item["to"], "?")}"'
                         + (f': {item["text"]}' if item.get('text') else ''))
        elif item['type'] == 'draw':
            lines.append(f'- {item["id"]} drawing ({len(item.get("points", []))} points)')
        elif 'x' in item:
            lines.append(f'- {item["id"]} {item["type"]} at ({item["x"]:g},{item["y"]:g}) {item["w"]:g}x{item["h"]:g} '
                         f'{item.get("color", "")}' + (f': {item["text"]}' if item.get('text') else ''))
        if sum(len(x) for x in lines) > limit:
            lines.append('(cut short)')
            break
    return '\n'.join(lines)


# ------------------------------------------------------------------ either

def normalize(fmt: str, text: str) -> str:
    if fmt == SHEET:
        return normalize_sheet(text)
    if fmt == WHITEBOARD:
        return normalize_board(text)
    return text


def summary(fmt: str, source: str) -> str:
    try:
        return sheet_summary(source) if fmt == SHEET else board_summary(source)
    except (ValueError, KeyError, TypeError):
        return ''


# ------------------------------------------------------------------ read-only pages

_PALETTE = {
    'yellow': ('#fff3b0', '#5c4a00'), 'green': ('#d8f5c4', '#1f4d12'), 'blue': ('#d4e6ff', '#123a6b'),
    'pink': ('#ffd9e6', '#6b1235'), 'purple': ('#e8dcff', '#3b1a6b'), 'orange': ('#ffe0c2', '#6b3412'),
    'gray': ('#e6e6e3', '#2b2d30'), 'white': ('#ffffff', '#16181a'),
}


def _fill(color: str) -> tuple[str, str]:
    if color in _PALETTE:
        return _PALETTE[color]
    return color, '#16181a'


def render_sheet(source: str) -> str:
    data = json.loads(source)
    sheet = Sheet(data)
    parts = []
    for tab in data.get('tabs', []):
        rows, cols = _bounds(tab['cells'])
        rows, cols = max(rows, 1), max(cols, 1)
        styles = tab.get('styles', {})
        head = ''.join(f'<th>{col_name(c)}</th>' for c in range(cols))
        body = []
        for r in range(rows):
            tds = []
            for c in range(cols):
                ref = f'{col_name(c)}{r + 1}'
                style = styles.get(ref, {})
                value = sheet.shown(tab['name'], ref) if ref in tab['cells'] else None
                classes = []
                if style.get('bold'):
                    classes.append('b')
                if isinstance(value, int | float) and not isinstance(value, bool):
                    classes.append('n')
                if style.get('align'):
                    classes.append(style['align'][0])
                tds.append(f'<td class="{" ".join(classes)}">{html.escape(shown_text(value, style.get("fmt")))}</td>')
            body.append(f'<tr><th>{r + 1}</th>{"".join(tds)}</tr>')
        parts.append(f'<section><h2>{html.escape(tab["name"])}</h2><div class="wrap"><table><thead><tr><th></th>{head}</tr>'
                     f'</thead><tbody>{"".join(body)}</tbody></table></div></section>')
    return (
        '<style>body{font-family:system-ui,sans-serif;margin:0;padding:16px}h2{font-size:14px;margin:18px 0 8px}'
        '.wrap{overflow:auto}table{border-collapse:collapse;font-size:13px}'
        'th,td{border:1px solid rgba(127,127,127,.3);padding:4px 8px;white-space:nowrap}'
        'th{background:rgba(127,127,127,.1);font-weight:500;font-size:11px;color:#777}'
        'td.n{text-align:right;font-variant-numeric:tabular-nums}td.b{font-weight:700}td.l{text-align:left}'
        'td.c{text-align:center}td.r{text-align:right}</style>' + ''.join(parts)
    )


def _wrap(text: str, width: float) -> list[str]:
    per_line = max(4, int(width / 7.5))
    lines: list[str] = []
    for para in text.split('\n'):
        words, line = para.split(' '), ''
        for word in words:
            if len(line) + len(word) + 1 > per_line and line:
                lines.append(line)
                line = word
            else:
                line = f'{line} {word}'.strip()
        lines.append(line)
    return lines[:40]


def _center(item: dict[str, Any]) -> tuple[float, float]:
    return item['x'] + item['w'] / 2, item['y'] + item['h'] / 2


def render_board(source: str) -> str:
    data = json.loads(source)
    items = data.get('items', [])
    boxes = {i['id']: i for i in items if 'x' in i}
    xs, ys = [0.0], [0.0]
    for item in items:
        if 'x' in item:
            xs += [item['x'], item['x'] + item['w']]
            ys += [item['y'], item['y'] + item['h']]
        for p in item.get('points', []):
            xs.append(p[0])
            ys.append(p[1])
    x0, y0, x1, y1 = min(xs) - 40, min(ys) - 40, max(xs) + 40, max(ys) + 40
    out = []
    for item in items:
        kind = item['type']
        stroke = _fill(item.get('color', 'gray'))[1] if item.get('color') in _PALETTE else item.get('color', '#555')
        if kind in ('arrow', 'line', 'draw'):
            if item.get('from') in boxes and item.get('to') in boxes:
                (ax, ay), (bx, by) = _center(boxes[item['from']]), _center(boxes[item['to']])
                pts = [[ax, ay], [bx, by]]
            else:
                pts = item.get('points', [])
            if len(pts) < 2:
                continue
            path = ' '.join(f'{p[0]},{p[1]}' for p in pts)
            marker = ' marker-end="url(#arrow)"' if kind == 'arrow' else ''
            out.append(f'<polyline points="{path}" fill="none" stroke="{html.escape(stroke)}" stroke-width="2"'
                       f' stroke-linecap="round" stroke-linejoin="round"{marker}/>')
            if item.get('text') and kind != 'draw':
                mx, my = (pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2
                out.append(f'<text x="{mx}" y="{my - 6}" text-anchor="middle" font-size="12">{html.escape(item["text"][:80])}</text>')
            continue
        fill, ink = _fill(item.get('color', 'yellow'))
        x, y, w, h = item['x'], item['y'], item['w'], item['h']
        if kind == 'ellipse':
            out.append(f'<ellipse cx="{x + w / 2}" cy="{y + h / 2}" rx="{w / 2}" ry="{h / 2}" fill="{html.escape(fill)}" stroke="{html.escape(ink)}" stroke-opacity=".35"/>')
        elif kind == 'rect':
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{html.escape(fill)}" stroke="{html.escape(ink)}" stroke-opacity=".35"/>')
        elif kind == 'note':
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{html.escape(fill)}" filter="url(#shadow)"/>')
        if item.get('text'):
            lines = _wrap(item['text'], w - 16)
            size = 18 if kind == 'text' else 14
            start = y + 24 if kind in ('note', 'text') else y + h / 2 - (len(lines) - 1) * 9 + 5
            anchor = 'start' if kind in ('note', 'text') else 'middle'
            tx = x + 10 if anchor == 'start' else x + w / 2
            spans = ''.join(f'<tspan x="{tx}" dy="{0 if i == 0 else size + 4}">{html.escape(line)}</tspan>' for i, line in enumerate(lines))
            color = ink if kind != 'text' else '#16181a'
            out.append(f'<text x="{tx}" y="{start}" font-size="{size}" fill="{color}" text-anchor="{anchor}">{spans}</text>')
    return (
        '<style>body{margin:0;background:#fafaf8}svg{display:block;width:100%;height:100vh;font-family:system-ui,sans-serif}</style>'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {x1 - x0} {y1 - y0}">'
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
        '<path d="M0 0L10 5L0 10z" fill="#555"/></marker>'
        '<filter id="shadow" x="-10%" y="-10%" width="130%" height="140%"><feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity=".18"/></filter></defs>'
        + ''.join(out) + '</svg>'
    )


def render(fmt: str, source: str) -> str:
    try:
        return render_sheet(source) if fmt == SHEET else render_board(source)
    except (ValueError, KeyError, TypeError):
        return '<p>This document could not be shown.</p>'
