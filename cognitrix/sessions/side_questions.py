"""Side questions: ask about a chat without touching it.

A side question is answered by the chat's agent model in one call, with the
saved conversation and what the running turn has done so far as context. It
never goes into the chat's history, never steers or stops a running turn, and
the model gets no tools, so it can only answer. Questions and answers are kept
per chat (``SideQuestion`` rows) so they come back after a restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from odbms import Model

logger = logging.getLogger('cognitrix.log')

MAX_QUESTION_CHARS = 2000
MAX_KEPT = 50
HISTORY_CHARS = 40_000
RUN_CHARS = 20_000
ANSWER_TIMEOUT = 120.0

SYSTEM_PROMPT = (
    'You answer a quick side question about the conversation below. You are '
    'not the agent doing the work and you cannot use tools, take actions or '
    'change anything. Answer only from what the conversation and the current '
    'progress show; if they do not say, say so plainly. Keep it short. Do not '
    'tell the person you are a side assistant unless they ask.'
)


class SideQuestion(Model):
    session_id: str
    user_id: str
    question: str
    answer: str = ''
    error: str = ''
    model: str = ''
    asked_at: str = ''
    """ISO time, UTC."""
    during_run: bool = False
    """Asked while the agent was working on a turn."""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def _clip_start(text: str, limit: int) -> str:
    """Keep the end: the newest part matters most."""
    return text if len(text) <= limit else '…' + text[-limit:]


def _short(value: Any, limit: int = 400) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit] + '…'


def history_text(chat: list[dict[str, Any]]) -> str:
    """The saved conversation as plain lines: messages, and tool calls by name."""
    lines: list[str] = []
    for m in chat or []:
        if not isinstance(m, dict) or m.get('type') == 'turn_timing':
            continue
        role = str(m.get('role') or '').lower()
        kind = m.get('type')
        if kind == 'tool_calls':
            names = ', '.join(str((tc or {}).get('name') or '?') for tc in (m.get('tool_calls') or []))
            if m.get('content'):
                lines.append(f'agent: {_short(m["content"], 1500)}')
            if names:
                lines.append(f'agent used tools: {names}')
            continue
        if role == 'tool' or kind == 'tool_calls_result':
            lines.append(f'tool result: {_short(m.get("content") or m.get("result") or "", 300)}')
            continue
        content = m.get('content')
        if isinstance(content, list):
            content = ' '.join(str(p.get('text', '')) for p in content if isinstance(p, dict))
        if not isinstance(content, str) or not content.strip():
            continue
        who = 'person' if role == 'user' else 'agent' if role == 'assistant' else role or 'note'
        lines.append(f'{who}: {_short(content, 3000)}')
    return '\n'.join(lines)


def run_text(events: list[dict[str, Any]]) -> str:
    """What the running turn has done so far, from its event log."""
    lines: list[str] = []
    for e in events or []:
        kind = e.get('type')
        if kind == 'generate' and isinstance(e.get('content'), str) and e['content'].strip():
            lines.append(f'agent (writing): {_short(e["content"], 2000)}')
        elif kind == 'tool':
            name = e.get('tool_name') or '?'
            if e.get('status') == 'started':
                lines.append(f'started tool {name} with {_short(e.get("params") or {}, 300)}')
            else:
                lines.append(f'tool {name} {e.get("status") or "finished"}: {_short(e.get("result") or "", 300)}')
        elif kind == 'approval_request':
            lines.append(f'waiting for the person to approve {e.get("tool_name") or "a tool"}')
        elif kind == 'question_request':
            lines.append(f'asked the person: {_short(e.get("prompt") or "", 300)}')
        elif kind == 'steer':
            lines.append(f'person added: {_short(e.get("content") or "", 500)}')
    return '\n'.join(lines)


def build_prompt(question: str, chat: list[dict[str, Any]], run_events: list[dict[str, Any]] | None,
                 earlier: list[SideQuestion] | None = None) -> list[dict[str, Any]]:
    parts = ['Conversation so far:\n' + (_clip_start(history_text(chat), HISTORY_CHARS) or '(empty)')]
    if run_events is not None:
        progress = run_text(run_events)
        parts.append(
            'The agent is working on the latest message right now. Progress so far:\n'
            + (_clip_start(progress, RUN_CHARS) or '(just started)')
        )
    messages: list[dict[str, Any]] = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': '\n\n'.join(parts)},
        {'role': 'assistant', 'content': 'Understood. What is your question?'},
    ]
    for q in (earlier or [])[-5:]:
        if q.answer:
            messages.append({'role': 'user', 'content': q.question})
            messages.append({'role': 'assistant', 'content': q.answer})
    messages.append({'role': 'user', 'content': question})
    return messages


def _load_llm(agent: Any):
    """The agent's own model, or the default provider when the agent runs on
    a coding CLI (those can't answer a single prompt without starting a job)."""
    from cognitrix.config import settings
    from cognitrix.providers.base import LLM
    from cognitrix.providers.harness import is_harness

    llm = getattr(agent, 'llm', None)
    if llm is not None and not is_harness(getattr(llm, 'provider', '')):
        return llm
    return LLM.load_llm(settings.ai_provider)


def as_dict(q: SideQuestion) -> dict[str, Any]:
    return {
        'id': str(q.id),
        'question': q.question,
        'answer': q.answer,
        'error': q.error,
        'model': q.model,
        'asked_at': q.asked_at,
        'during_run': bool(q.during_run),
    }


async def list_for(session_id: str, user_id: str) -> list[SideQuestion]:
    rows = await SideQuestion.find({'session_id': session_id, 'user_id': user_id}) or []
    rows = sorted(rows, key=lambda r: r.asked_at or '')
    return rows[-MAX_KEPT:]


async def clear_for(session_id: str, user_id: str) -> int:
    rows = await SideQuestion.find({'session_id': session_id, 'user_id': user_id}) or []
    for row in rows:
        await row.delete()
    return len(rows)


async def ask(*, session: Any, agent: Any, user_id: str, question: str,
              run_events: list[dict[str, Any]] | None, save: bool = True) -> SideQuestion:
    """Answer one side question. Model failures come back in ``error``."""
    question = question.strip()[:MAX_QUESTION_CHARS]
    earlier = await list_for(str(session.id), user_id)
    row = SideQuestion(
        session_id=str(session.id), user_id=user_id, question=question,
        asked_at=_now(), during_run=run_events is not None,
    )
    try:
        llm = _load_llm(agent)
    except Exception:
        logger.exception('Side question: could not load a model')
        llm = None
    if llm is None:
        row.error = 'No model is available to answer.'
    else:
        row.model = f'{llm.provider}/{llm.model}' if getattr(llm, 'model', '') else str(llm.provider)
        prompt = build_prompt(question, list(session.chat or []), run_events, earlier)
        try:
            response = await asyncio.wait_for(llm(prompt, stream=False), timeout=ANSWER_TIMEOUT)
        except TimeoutError:
            response = None
            row.error = 'The model took too long to answer.'
        except Exception as exc:
            logger.warning('Side question failed: %s', exc)
            response = None
            row.error = 'The model could not be reached.'
        if response is not None:
            if getattr(response, 'error', None):
                row.error = str(response.error)[:500]
            else:
                row.answer = (getattr(response, 'llm_response', '') or '').strip()
                if not row.answer:
                    row.error = 'The model gave no answer.'
    if save:
        await row.save()
        # Keep the list short.
        rows = await SideQuestion.find({'session_id': str(session.id), 'user_id': user_id}) or []
        for old in sorted(rows, key=lambda r: r.asked_at or '')[:-MAX_KEPT]:
            await old.delete()
    return row
