"""Check an agent's card and turn it into one clean shape.

What the agent may send (``normalize`` takes any of these):

- A nested tree: ``{"type": "Card", "props": {"title": ...}, "children": [...]}``.
  Props may also sit directly on the element (``{"type": "Field", "label": ...}``).
- json-render's flat form: ``{"root": "c", "elements": {"c": {"type", "props", "children": [ids]}}}``.
- A bare card: ``{"title": ..., "children": [...]}``.

What comes out (stored on the tool outcome and drawn by the chat)::

    {"id", "title", "subtitle", "blocks": [block, ...]}

with blocks of type ``text``, ``fields``, ``table``, ``chart``, ``image``,
``divider``, ``actions`` and ``section`` (a titled group of other blocks, one
level deep). Anything wrong is collected as a readable issue with its path, so
the model can fix the card on its next step.
"""
from __future__ import annotations

import math
import re
import secrets
from typing import Any

MAX_ELEMENTS = 80
MAX_TEXT = 4000
MAX_LABEL = 200
MAX_TABLE_ROWS = 50
MAX_TABLE_COLUMNS = 8
MAX_CHARTS = 4
MAX_CHART_POINTS = 40
MAX_CHART_SERIES = 4
MAX_BUTTONS = 6
MAX_IMAGE_DATA = 1_000_000
CHART_KINDS = ('bar', 'line', 'area', 'pie')
BUTTON_STYLES = ('default', 'primary', 'danger')
TONES = ('neutral', 'good', 'bad')

# json-render, Chat SDK and Adaptive Card names for the same things.
_ALIASES = {
    'card': 'card', 'section': 'section', 'container': 'section', 'group': 'section',
    'text': 'text', 'cardtext': 'text', 'markdown': 'text', 'textblock': 'text', 'paragraph': 'text',
    'fields': 'fields', 'factset': 'fields', 'stats': 'fields',
    'field': 'field', 'fact': 'field', 'stat': 'field', 'metric': 'field',
    'table': 'table', 'chart': 'chart', 'image': 'image', 'divider': 'divider', 'separator': 'divider',
    'actions': 'actions', 'actionset': 'actions', 'buttons': 'actions',
    'button': 'button', 'linkbutton': 'linkbutton', 'link': 'linkbutton',
}
_ACTION_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')
_DATA_IMAGE = re.compile(r'^data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+$')


class CardError(ValueError):
    """The card could not be shown; ``issues`` says why, one line each."""

    def __init__(self, issues: list[str]):
        self.issues = issues[:20]
        super().__init__('; '.join(self.issues))


class _Checker:
    def __init__(self) -> None:
        self.issues: list[str] = []
        self.count = 0
        self.charts = 0
        self.buttons = 0

    def bad(self, path: str, message: str) -> None:
        self.issues.append(f'{path}: {message}')


def _kind(node: dict[str, Any]) -> str:
    return _ALIASES.get(re.sub(r'[\s_-]', '', str(node.get('type') or '')).lower(), '')


def _props(node: dict[str, Any]) -> dict[str, Any]:
    props = node.get('props')
    merged = {k: v for k, v in node.items() if k not in ('type', 'props', 'children', 'key', 'id')}
    if isinstance(props, dict):
        merged.update(props)
    return merged


def _text(value: Any, limit: int = MAX_LABEL) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()[:limit]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        cleaned = re.sub(r'[,$€£%\s]', '', value)
        try:
            number = float(cleaned)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _unflatten(spec: dict[str, Any], c: _Checker) -> dict[str, Any] | None:
    """json-render's {root, elements} map -> a nested tree."""
    elements = spec.get('elements')
    if not isinstance(elements, dict):
        c.bad('elements', 'must be an object of id -> element')
        return None
    seen: set[str] = set()

    def build(key: str, path: str) -> dict[str, Any] | None:
        if key in seen:
            c.bad(path, f'element "{key}" is used twice (or loops)')
            return None
        seen.add(key)
        raw = elements.get(key)
        if not isinstance(raw, dict):
            c.bad(path, f'no element with id "{key}"')
            return None
        node = dict(raw)
        kids = raw.get('children') or []
        node['children'] = [
            child for i, kid in enumerate(kids if isinstance(kids, list) else [])
            if (child := build(str(kid), f'elements.{kid}')) is not None
        ] if isinstance(kids, list) else []
        if kids and not isinstance(kids, list):
            c.bad(f'elements.{key}.children', 'must be a list of element ids')
        return node

    return build(str(spec.get('root') or ''), 'root')


def _children(node: dict[str, Any]) -> list[Any]:
    kids = node.get('children')
    return kids if isinstance(kids, list) else []


# The short form: {"title", "fields": [...], "chart": {...}, "table": {...},
# "text": "...", "buttons": [...]}, drawn in the order the keys come.
_SHORT_KEYS = {
    'fields': 'Fields', 'metrics': 'Fields', 'stats': 'Fields', 'facts': 'Fields',
    'chart': 'Chart', 'charts': 'Chart', 'table': 'Table', 'tables': 'Table',
    'text': 'Text', 'summary': 'Text', 'body': 'Text', 'markdown': 'Text', 'note': 'Text',
    'image': 'Image', 'images': 'Image',
    'buttons': 'Actions', 'actions': 'Actions', 'sections': 'Section',
}
_ROOT_KEYS = {'type', 'props', 'children', 'key', 'id', 'title', 'subtitle', 'description'}


def _expand_short(root: dict[str, Any], c: _Checker) -> list[Any]:
    """Children for a card written in the short form."""
    props = _props(root)
    kids: list[Any] = []
    for key, value in props.items():
        kind = _SHORT_KEYS.get(key)
        if kind is None:
            if key not in _ROOT_KEYS and isinstance(value, dict | list):
                c.bad(key, f'unknown part "{key}"; use fields, chart, table, text, image, buttons or children')
            continue
        items = value if isinstance(value, list) else [value]
        if kind == 'Fields':
            kids.append({'type': 'Fields', 'children': [
                {'type': 'Field', 'props': item} if isinstance(item, dict) else item for item in items]})
        elif kind == 'Actions':
            kids.append({'type': 'Actions', 'children': [
                {'type': 'Button', 'props': item} if isinstance(item, dict) and 'type' not in item else item
                for item in items]})
        elif kind == 'Text':
            kids.extend({'type': 'Text', 'props': {'text': item}} if isinstance(item, str) else item for item in items)
        else:
            kids.extend({'type': kind, 'props': item} if isinstance(item, dict) and 'type' not in item
                        or (isinstance(item, dict) and kind == 'Chart') else item for item in items)
    return kids


def _field(props: dict[str, Any], path: str, c: _Checker) -> dict[str, Any] | None:
    label = _text(props.get('label') or props.get('title') or props.get('name'))
    value = _text(props.get('value') if props.get('value') is not None else props.get('text'))
    if not label:
        c.bad(path, 'a Field needs a label')
        return None
    tone = str(props.get('tone') or props.get('trend') or props.get('sentiment') or 'neutral').lower()
    tone = {'positive': 'good', 'negative': 'bad', 'up': 'neutral', 'down': 'neutral', 'flat': 'neutral'}.get(tone, tone)
    if tone not in TONES:
        c.bad(path, f'tone must be one of {", ".join(TONES)}')
        tone = 'neutral'
    item = {'label': label, 'value': value or '–', 'tone': tone}
    change = _text(props.get('change') or props.get('delta'), 40)
    if change:
        item['change'] = change
    return item


def _table(props: dict[str, Any], path: str, c: _Checker) -> dict[str, Any] | None:
    raw_cols = props.get('columns') or props.get('headers') or []
    raw_rows = props.get('rows') or props.get('data') or []
    if not isinstance(raw_cols, list) or not isinstance(raw_rows, list):
        c.bad(path, 'columns and rows must be lists')
        return None
    keys: list[str] = []
    labels: list[str] = []
    for col in raw_cols:
        if isinstance(col, dict):
            key = _text(col.get('key') or col.get('id') or col.get('label') or col.get('header'))
            keys.append(key)
            labels.append(_text(col.get('label') or col.get('header') or key))
        else:
            keys.append(_text(col))
            labels.append(_text(col))
    if not labels and raw_rows and isinstance(raw_rows[0], dict):
        keys = labels = [_text(k) for k in raw_rows[0]]
    if not labels:
        c.bad(path, 'a Table needs columns')
        return None
    if len(labels) > MAX_TABLE_COLUMNS:
        c.bad(path, f'at most {MAX_TABLE_COLUMNS} columns (got {len(labels)})')
        return None
    if len(raw_rows) > MAX_TABLE_ROWS:
        c.bad(path, f'at most {MAX_TABLE_ROWS} rows (got {len(raw_rows)}); show the top rows and link to the rest')
        return None
    rows: list[list[str]] = []
    for i, row in enumerate(raw_rows):
        if isinstance(row, dict):
            cells = [row.get(k, row.get(label, '')) for k, label in zip(keys, labels, strict=False)]
        elif isinstance(row, list):
            cells = list(row)
        else:
            c.bad(f'{path}.rows[{i}]', 'each row must be a list of cells or an object keyed by column')
            continue
        if len(cells) != len(labels):
            c.bad(f'{path}.rows[{i}]', f'has {len(cells)} cells but there are {len(labels)} columns')
            continue
        rows.append([_text(cell) for cell in cells])
    numeric = [
        bool(rows) and all(_number(r[i]) is not None or r[i] in ('', '–', '-') for r in rows)
        for i in range(len(labels))
    ]
    out = {'type': 'table', 'columns': labels, 'rows': rows, 'numeric': numeric}
    title = _text(props.get('title'))
    if title:
        out['title'] = title
    return out


def _chart(props: dict[str, Any], path: str, c: _Checker) -> dict[str, Any] | None:
    c.charts += 1
    if c.charts > MAX_CHARTS:
        c.bad(path, f'at most {MAX_CHARTS} charts on one card')
        return None
    kind = str(props.get('kind') or props.get('chartType') or props.get('type') or 'bar').lower()
    if kind not in CHART_KINDS:
        c.bad(path, f'kind must be one of {", ".join(CHART_KINDS)} (got "{kind}")')
        return None
    labels: list[str] = []
    series: list[dict[str, Any]] = []
    data = props.get('data')
    if isinstance(data, list) and data and isinstance(data[0], dict):
        # [{label, value}] or [{x, y}] or [{label, a: 1, b: 2}]
        first = data[0]
        label_key = next((k for k in ('label', 'name', 'x', 'category') if k in first), None)
        if label_key is None:
            c.bad(path, 'data points need a label (or name, or x)')
            return None
        value_keys = [k for k in first if k != label_key and k not in ('color',)]
        labels = [_text(p.get(label_key), 60) for p in data]
        for key in value_keys[:MAX_CHART_SERIES]:
            name = '' if key in ('value', 'y') else _text(key, 60)
            series.append({'name': name, 'values': [p.get(key) for p in data]})
    else:
        labels = [_text(v, 60) for v in (props.get('labels') or props.get('categories') or [])]
        raw_series = props.get('series')
        if isinstance(raw_series, list) and raw_series:
            for i, s in enumerate(raw_series):
                if isinstance(s, dict):
                    values = s.get('values') or s.get('data') or []
                    if isinstance(values, list) and values and all(isinstance(p, dict) for p in values):
                        # a series of points: [{label, value}]
                        if not labels:
                            labels = [_text(p.get('label', p.get('x', p.get('name'))), 60) for p in values]
                        values = [p.get('value', p.get('y')) for p in values]
                    series.append({'name': _text(s.get('name') or s.get('label'), 60), 'values': values})
                else:
                    c.bad(f'{path}.series[{i}]', 'each series is {"name", "values": [numbers]}')
        elif isinstance(props.get('values'), list):
            series.append({'name': '', 'values': props['values']})
    if not labels or not series:
        c.bad(path, 'a Chart needs labels and values: {"labels": [...], "series": [{"name", "values": [...]}]} '
                    'or {"data": [{"label", "value"}]}')
        return None
    if len(labels) > MAX_CHART_POINTS:
        c.bad(path, f'at most {MAX_CHART_POINTS} points (got {len(labels)})')
        return None
    if len(series) > MAX_CHART_SERIES:
        c.bad(path, f'at most {MAX_CHART_SERIES} series (got {len(series)})')
        return None
    if kind == 'pie' and len(series) > 1:
        c.bad(path, 'a pie chart takes one series')
        return None
    clean: list[dict[str, Any]] = []
    for i, s in enumerate(series):
        values = s['values'] if isinstance(s['values'], list) else []
        if len(values) != len(labels):
            c.bad(f'{path}.series[{i}]', f'has {len(values)} values but there are {len(labels)} labels')
            return None
        numbers = [_number(v) for v in values]
        if any(n is None for n in numbers):
            c.bad(f'{path}.series[{i}]', 'values must all be numbers')
            return None
        if kind == 'pie' and any(n < 0 for n in numbers if n is not None):
            c.bad(f'{path}.series[{i}]', 'pie values cannot be negative')
            return None
        clean.append({'name': s['name'], 'values': numbers})
    out: dict[str, Any] = {'type': 'chart', 'kind': kind, 'labels': labels, 'series': clean}
    for key in ('title', 'unit'):
        value = _text(props.get(key), 80)
        if value:
            out[key] = value
    return out


def _button(kind: str, props: dict[str, Any], path: str, c: _Checker) -> dict[str, Any] | None:
    c.buttons += 1
    if c.buttons > MAX_BUTTONS:
        c.bad(path, f'at most {MAX_BUTTONS} buttons on one card')
        return None
    label = _text(props.get('label') or props.get('text') or props.get('title'), 60)
    if not label:
        c.bad(path, 'a button needs a label')
        return None
    url = str(props.get('url') or props.get('href') or '').strip()
    if kind == 'linkbutton' or url:
        if not re.match(r'^https?://\S+$', url) and not url.startswith('/'):
            c.bad(path, 'a link button needs an http(s) url')
            return None
        return {'label': label, 'url': url[:2000]}
    action = str(props.get('action') or props.get('actionId') or props.get('id') or '').strip()
    if not action:
        action = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')[:64] or 'button'
    if not _ACTION_ID.match(action):
        c.bad(path, 'action must be 1-64 letters, digits, _ . : or -')
        return None
    style = str(props.get('style') or 'default').lower()
    if style not in BUTTON_STYLES:
        style = 'default'
    out = {'label': label, 'action': action, 'style': style}
    value = _text(props.get('value') or props.get('message') or props.get('prompt'), 200)
    if value:
        out['value'] = value
    return out


def _blocks(nodes: list[Any], path: str, c: _Checker, depth: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for i, node in enumerate(nodes):
        where = f'{path}[{i}]'
        c.count += 1
        if c.count > MAX_ELEMENTS:
            c.bad(where, f'a card holds at most {MAX_ELEMENTS} elements')
            return blocks
        if isinstance(node, str):
            if node.strip():
                blocks.append({'type': 'text', 'text': node.strip()[:MAX_TEXT]})
            continue
        if not isinstance(node, dict):
            c.bad(where, 'each child must be an element object')
            continue
        kind = _kind(node)
        props = _props(node)
        if not kind:
            c.bad(where, f'unknown element type "{node.get("type")}"; use Section, Text, Fields, Field, '
                         'Table, Chart, Image, Divider, Actions, Button or LinkButton')
            continue
        if kind in ('card', 'section'):
            if depth >= 1:
                # Cards can't nest deeply: flatten a section in a section.
                title = _text(props.get('title'))
                if title:
                    blocks.append({'type': 'text', 'text': f'**{title}**'})
                blocks.extend(_blocks(_children(node), f'{where}.children', c, depth))
                continue
            inner = _blocks(_children(node), f'{where}.children', c, depth + 1)
            section: dict[str, Any] = {'type': 'section', 'blocks': inner}
            title = _text(props.get('title'))
            if title:
                section['title'] = title
            if inner or title:
                blocks.append(section)
        elif kind == 'text':
            text = _text(props.get('text') or props.get('content') or props.get('markdown') or props.get('value'),
                         MAX_TEXT)
            if text:
                blocks.append({'type': 'text', 'text': text})
            else:
                c.bad(where, 'a Text needs text')
        elif kind in ('fields', 'field'):
            raw = _children(node) if kind == 'fields' else [node]
            if kind == 'fields' and not raw and isinstance(props.get('items') or props.get('fields'), list):
                raw = props.get('items') or props.get('fields')
            items = []
            for j, child in enumerate(raw):
                if isinstance(child, dict):
                    item = _field(_props(child), f'{where}.children[{j}]', c)
                    if item:
                        items.append(item)
            if items:
                if blocks and blocks[-1]['type'] == 'fields' and kind == 'field':
                    blocks[-1]['items'].append(items[0])
                else:
                    blocks.append({'type': 'fields', 'items': items[:12]})
        elif kind == 'table':
            table = _table(props, where, c)
            if table:
                blocks.append(table)
        elif kind == 'chart':
            chart = _chart(props, where, c)
            if chart:
                blocks.append(chart)
        elif kind == 'image':
            url = str(props.get('url') or props.get('src') or '').strip()
            if re.match(r'^https://\S+$', url) or (_DATA_IMAGE.match(url) and len(url) <= MAX_IMAGE_DATA):
                blocks.append({'type': 'image', 'url': url, 'alt': _text(props.get('alt') or props.get('title'))})
            else:
                c.bad(where, 'an Image needs an https url or a small data:image/... base64 url')
        elif kind == 'divider':
            blocks.append({'type': 'divider'})
        elif kind in ('actions', 'button', 'linkbutton'):
            raw = _children(node) if kind == 'actions' else [node]
            buttons = []
            for j, child in enumerate(raw):
                if isinstance(child, dict):
                    button = _button(_kind(child), _props(child), f'{where}.children[{j}]', c)
                    if button:
                        buttons.append(button)
            if buttons:
                if blocks and blocks[-1]['type'] == 'actions' and kind != 'actions':
                    blocks[-1]['buttons'].extend(buttons)
                else:
                    blocks.append({'type': 'actions', 'buttons': buttons})
    return blocks


def normalize(spec: Any) -> dict[str, Any]:
    """Check a card and return its clean shape, or raise CardError."""
    c = _Checker()
    if not isinstance(spec, dict):
        raise CardError(['card: must be a JSON object'])
    root: dict[str, Any] | None = spec
    if 'elements' in spec and 'root' in spec:
        root = _unflatten(spec, c)
    if root is None:
        raise CardError(c.issues or ['card: no root element'])
    if root.get('type') and _kind(root) != 'card':
        # A single element given on its own: wrap it.
        root = {'type': 'Card', 'children': [root]}
    props = _props(root)
    title = _text(props.get('title'))
    subtitle = _text(props.get('subtitle') or props.get('description'), 300)
    kids = _children(root) or _expand_short(root, c)
    blocks = _blocks(kids, 'children', c, 0)
    if not title and not blocks:
        c.bad('card', 'needs a title or at least one child')
    if c.issues:
        raise CardError(c.issues)
    return {'id': 'card_' + secrets.token_hex(5), 'title': title, 'subtitle': subtitle, 'blocks': blocks}


def _walk(blocks: list[dict[str, Any]]):
    for block in blocks:
        yield block
        if block['type'] == 'section':
            yield from _walk(block['blocks'])


def buttons_of(card: dict[str, Any]) -> list[dict[str, Any]]:
    """The card's action buttons (not links), in order."""
    return [b for block in _walk(card.get('blocks') or []) if block['type'] == 'actions'
            for b in block['buttons'] if 'action' in b]


def button_message(card: dict[str, Any], button: dict[str, Any]) -> str:
    """What the agent is told when someone presses a button."""
    extra = f', value: {button["value"]}' if button.get('value') else ''
    title = card.get('title') or 'card'
    return f'[Pressed "{button["label"]}" on card "{title}" (action: {button["action"]}{extra})]'


def _bar(value: float, top: float, width: int = 12) -> str:
    return '█' * max(1 if value > 0 else 0, round(value / top * width)) if top > 0 else ''


def _fmt(number: float) -> str:
    return f'{int(number):,}' if float(number).is_integer() else f'{number:,.2f}'.rstrip('0').rstrip('.')


def _chart_text(block: dict[str, Any]) -> str:
    labels = block['labels']
    width = max(len(label) for label in labels)
    unit = f' {block["unit"]}' if block.get('unit') else ''
    lines: list[str] = []
    if block['kind'] == 'pie':
        values = block['series'][0]['values']
        total = sum(values) or 1
        for label, value in zip(labels, values, strict=False):
            lines.append(f'{label.ljust(width)}  {_fmt(value)}{unit} ({value / total:.0%})')
    else:
        top = max((v for s in block['series'] for v in s['values']), default=0)
        for s in block['series']:
            if s['name'] and len(block['series']) > 1:
                lines.append(s['name'])
            for label, value in zip(labels, s['values'], strict=False):
                lines.append(f'{label.ljust(width)} {_bar(value, top).ljust(12)} {_fmt(value)}{unit}')
    return '\n'.join(lines)


def _table_text(block: dict[str, Any]) -> str:
    cols = block['columns']
    rows = block['rows']
    widths = [min(24, max(len(cols[i]), *(len(r[i]) for r in rows))) if rows else len(cols[i])
              for i in range(len(cols))]

    def line(cells: list[str]) -> str:
        out = []
        for i, cell in enumerate(cells):
            cell = cell[:widths[i]]
            out.append(cell.rjust(widths[i]) if block['numeric'][i] else cell.ljust(widths[i]))
        return '  '.join(out).rstrip()

    return '\n'.join([line(cols), *(line(r) for r in rows)])


def _block_md(block: dict[str, Any]) -> list[str]:
    kind = block['type']
    if kind == 'text':
        return [block['text']]
    if kind == 'fields':
        return ['\n'.join(
            f'**{f["label"]}:** {f["value"]}' + (f' ({f["change"]})' if f.get('change') else '')
            for f in block['items'])]
    if kind == 'table':
        head = [f'**{block["title"]}**'] if block.get('title') else []
        return [*head, f'```\n{_table_text(block)}\n```']
    if kind == 'chart':
        head = [f'**{block["title"]}**'] if block.get('title') else []
        return [*head, f'```\n{_chart_text(block)}\n```']
    if kind == 'image':
        return [f'[{block.get("alt") or "Image"}]({block["url"]})'] if block['url'].startswith('https://') else []
    if kind == 'divider':
        return ['———']
    if kind == 'actions':
        links = [f'[{b["label"]}]({b["url"]})' for b in block['buttons'] if 'url' in b and b['url'].startswith('http')]
        return [' · '.join(links)] if links else []
    if kind == 'section':
        head = [f'**{block["title"]}**'] if block.get('title') else []
        return [*head, *(part for child in block['blocks'] for part in _block_md(child))]
    return []


def to_markdown(card: dict[str, Any], numbered_buttons: bool = False) -> str:
    """The card as Markdown, for chat apps and anywhere cards can't be drawn.

    Action buttons are left out (chat apps send them as real buttons), unless
    ``numbered_buttons``: then they are listed so a person can type the choice.
    """
    parts: list[str] = []
    if card.get('title'):
        parts.append(f'**{card["title"]}**' + (f'\n_{card["subtitle"]}_' if card.get('subtitle') else ''))
    elif card.get('subtitle'):
        parts.append(f'_{card["subtitle"]}_')
    for block in card.get('blocks') or []:
        parts.extend(p for p in _block_md(block) if p)
    if numbered_buttons:
        options = buttons_of(card)
        if options:
            parts.append('Reply with: ' + ' · '.join(f'"{b["label"]}"' for b in options))
    return '\n\n'.join(parts)
