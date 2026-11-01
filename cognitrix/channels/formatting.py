"""Turn an agent's Markdown reply into what each chat app renders.

Models answer in Markdown. Telegram renders a small HTML subset and Slack its
own "mrkdwn", so the common pieces (code, bold, italics, links, headings) are
converted and everything else is left as plain text.
"""
from __future__ import annotations

import html
import re

_FENCE = re.compile(r'```[^\n`]*\n?(.*?)```', re.S)
_INLINE_CODE = re.compile(r'`([^`\n]+)`')
_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://[^)\s]+)\)')
_BOLD = re.compile(r'\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__')
_ITALIC = re.compile(r'(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])')
_HEADING = re.compile(r'^#{1,6}\s+(.+?)\s*#*\s*$', re.M)
_PLACEHOLDER = '\x00{}\x00'
_PLACEHOLDER_RE = re.compile(r'\x00(\d+)\x00')


_THINK = re.compile(r'<think>.*?(?:</think>|$)', re.S)


def strip_reasoning(text: str) -> str:
    """Drop the model's <think> blocks (an unclosed one runs to the end)."""
    return _THINK.sub('', text or '').strip()


def split_text(text: str, limit: int) -> list[str]:
    """Split on paragraph, then line, then hard boundaries so no part exceeds limit."""
    text = (text or '').strip()
    if not text:
        return []
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind('\n\n', 0, limit)
        if cut < limit // 2:
            cut = text.rfind('\n', 0, limit)
        if cut < limit // 2:
            cut = text.rfind(' ', 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


def _protect(text: str, render_block, render_inline) -> tuple[str, list[str]]:
    """Swap code spans for placeholders so other rules leave them alone."""
    saved: list[str] = []

    def keep(rendered: str) -> str:
        saved.append(rendered)
        return _PLACEHOLDER.format(len(saved) - 1)

    text = _FENCE.sub(lambda m: keep(render_block(m.group(1).rstrip('\n'))), text)
    text = _INLINE_CODE.sub(lambda m: keep(render_inline(m.group(1))), text)
    return text, saved


def _restore(text: str, saved: list[str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: saved[int(m.group(1))], text)


def to_telegram_html(markdown: str) -> str:
    """Markdown -> Telegram's HTML parse mode (everything else escaped)."""
    text, saved = _protect(
        markdown or '',
        lambda code: f'<pre>{html.escape(code, quote=False)}</pre>',
        lambda code: f'<code>{html.escape(code, quote=False)}</code>',
    )
    text = html.escape(text, quote=False)
    text = _LINK.sub(lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', text)
    text = _HEADING.sub(lambda m: f'<b>{m.group(1)}</b>', text)
    text = _BOLD.sub(lambda m: f'<b>{m.group(1) or m.group(2)}</b>', text)
    text = _ITALIC.sub(lambda m: f'<i>{m.group(1)}</i>', text)
    return _restore(text, saved)


def _slack_escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def to_slack_mrkdwn(markdown: str) -> str:
    """Markdown -> Slack mrkdwn."""
    text, saved = _protect(
        markdown or '',
        lambda code: f'```{_slack_escape(code)}```',
        lambda code: f'`{_slack_escape(code)}`',
    )
    text = _slack_escape(text)
    links: list[str] = []

    def link(m: re.Match) -> str:
        links.append(f'<{m.group(2)}|{m.group(1)}>')
        return _PLACEHOLDER.format(f'L{len(links) - 1}')

    text = _LINK.sub(link, text)
    text = _ITALIC.sub(lambda m: f'_{m.group(1)}_', text)
    text = _HEADING.sub(lambda m: f'*{m.group(1)}*', text)
    text = _BOLD.sub(lambda m: f'*{m.group(1) or m.group(2)}*', text)
    text = re.sub(r'\x00L(\d+)\x00', lambda m: links[int(m.group(1))], text)
    return _restore(text, saved)
