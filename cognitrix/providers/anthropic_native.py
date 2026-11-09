"""Talk to Claude through Anthropic's own Messages API.

Every other provider goes through the OpenAI-compatible layer in base.py.
Anthropic's OpenAI-compatible endpoint has no prompt caching, so a long tool
loop pays full input price for the whole conversation on every step. This
module sends the same request natively with cache breakpoints: one on the
system prompt (which also covers the tool list) and one that follows the end
of the conversation.

The functions here take the OpenAI-shaped messages and tools that base.py
already builds and return the same LLMResponse objects, so callers don't know
which path ran.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from cognitrix.errors import ExecutionControlError
from cognitrix.utils.llm_response import LLMResponse

logger = logging.getLogger('cognitrix.log')

ANTHROPIC_HOST = 'api.anthropic.com'
HELICONE_ANTHROPIC_BASE = 'https://anthropic.helicone.ai'

_CLAUDE_VERSION = re.compile(r'^claude-(?:opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d{1,2}))?(?!\d)')
_EFFORTS = {'minimal': 'low', 'low': 'low', 'medium': 'medium', 'high': 'high', 'xhigh': 'xhigh'}
_STOP_REASONS = {
    'end_turn': 'stop', 'stop_sequence': 'stop', 'tool_use': 'tool_calls',
    'max_tokens': 'length', 'refusal': 'content_filter', 'pause_turn': 'stop',
}
_CACHE = {'type': 'ephemeral'}

_clients: dict[tuple[str, str, tuple[tuple[str, str], ...]], tuple[Any, Any]] = {}


def enabled() -> bool:
    return os.getenv('COGNITRIX_ANTHROPIC_NATIVE', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def can_handle(llm: Any, response_format: Any = None) -> bool:
    """True when this request can go to the Messages API as is."""
    if not enabled() or getattr(llm, 'provider', '') != 'anthropic':
        return False
    # A custom base URL is somebody's proxy; it may only speak the OpenAI shape.
    if ANTHROPIC_HOST not in str(getattr(llm, 'base_url', '') or ''):
        return False
    # Structured-output requests keep the path they were written against.
    if response_format or getattr(llm, 'extra_body', None):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _version(model: str) -> tuple[int, int] | None:
    m = _CLAUDE_VERSION.match((model or '').lower().rsplit('/', 1)[-1])
    return (int(m.group(1)), int(m.group(2) or 0)) if m else None


def _client(base_url: str, api_key: str, headers: dict[str, str] | None):
    import anthropic

    key = (base_url, api_key, tuple(sorted((headers or {}).items())))
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    cached = _clients.get(key)
    if cached and cached[0] is loop:
        return cached[1]
    # Retries are done here so task budgets see every attempt.
    client = anthropic.AsyncAnthropic(
        api_key=api_key, base_url=base_url, default_headers=headers or None, max_retries=0,
    )
    _clients[key] = (loop, client)
    return client


# --- request conversion ------------------------------------------------------

def _image_block(url: str) -> dict[str, Any] | None:
    if not url:
        return None
    if url.startswith('data:'):
        head, _, data = url.partition(',')
        media = head[5:].split(';')[0] or 'image/png'
        return {'type': 'image', 'source': {'type': 'base64', 'media_type': media, 'data': data}}
    return {'type': 'image', 'source': {'type': 'url', 'url': url}}


def _blocks(content: Any) -> list[dict[str, Any]]:
    """OpenAI message content (string or parts) as Anthropic content blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if content.strip() else []
    blocks: list[dict[str, Any]] = []
    for part in content if isinstance(content, list) else [content]:
        if isinstance(part, str):
            if part.strip():
                blocks.append({'type': 'text', 'text': part})
        elif isinstance(part, dict):
            if part.get('type') == 'text' and str(part.get('text') or '').strip():
                blocks.append({'type': 'text', 'text': str(part['text'])})
            elif part.get('type') == 'image_url':
                image = part.get('image_url')
                block = _image_block(image.get('url', '') if isinstance(image, dict) else str(image or ''))
                if block:
                    blocks.append(block)
    return blocks


def _text(content: Any) -> str:
    return '\n'.join(b['text'] for b in _blocks(content) if b['type'] == 'text')


def convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    converted = []
    for tool in tools or []:
        fn = tool.get('function') if isinstance(tool, dict) else None
        if not fn or not fn.get('name'):
            continue
        schema = fn.get('parameters') or {'type': 'object', 'properties': {}}
        converted.append({
            'name': fn['name'],
            'description': fn.get('description') or '',
            'input_schema': schema,
        })
    return converted


def convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split OpenAI-shaped messages into (system text, Anthropic messages).

    System messages at the start become the system prompt. Later ones stay in
    place as user notes, so the prompt prefix does not change as a chat grows.
    Tool results are tied to the call just before them; an orphan on either
    side is repaired because the API rejects unmatched pairs.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    started = False
    # Tool calls from the last assistant message still waiting for a result.
    waiting: list[str] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]['role'] == role:
            out[-1]['content'].extend(blocks)
        else:
            out.append({'role': role, 'content': list(blocks)})

    def close_waiting() -> None:
        # A call with no recorded result (interrupted turn, folded history).
        if waiting:
            add('user', [
                {'type': 'tool_result', 'tool_use_id': tid, 'content': '(no result was recorded)'}
                for tid in waiting
            ])
            waiting.clear()

    for msg in messages:
        role = msg.get('role', 'user')
        content = msg.get('content')
        if role == 'system':
            text = _text(content)
            if not text:
                continue
            if not started:
                system_parts.append(text)
            else:
                close_waiting()
                add('user', [{'type': 'text', 'text': f'[System note]\n{text}'}])
            continue
        started = True
        if role == 'tool':
            tool_id = str(msg.get('tool_call_id') or '')
            result = _blocks(content) or [{'type': 'text', 'text': '(no output)'}]
            if tool_id in waiting:
                waiting.remove(tool_id)
                # Results come first in the user message, before any other text.
                block = {'type': 'tool_result', 'tool_use_id': tool_id, 'content': result}
                if out and out[-1]['role'] == 'user':
                    lead = sum(1 for b in out[-1]['content'] if b['type'] == 'tool_result')
                    out[-1]['content'].insert(lead, block)
                else:
                    out.append({'role': 'user', 'content': [block]})
            else:
                close_waiting()
                add('user', [{'type': 'text', 'text': '[Tool result]\n' + (_text(content) or '(no output)')}])
            continue
        if role == 'assistant':
            close_waiting()
            blocks = _blocks(content)
            for i, call in enumerate(msg.get('tool_calls') or []):
                fn = call.get('function') or {}
                try:
                    args = json.loads(fn.get('arguments') or '{}')
                except (TypeError, ValueError):
                    args = {}
                tool_id = str(call.get('id') or f'call_{i}')
                blocks.append({
                    'type': 'tool_use', 'id': tool_id, 'name': str(fn.get('name') or 'tool'),
                    'input': args if isinstance(args, dict) else {},
                })
                waiting.append(tool_id)
            add('assistant', blocks)
            continue
        # A user message that arrives while calls are waiting: results first.
        close_waiting()
        add('user', _blocks(content))
    close_waiting()

    if not out or out[0]['role'] != 'user':
        out.insert(0, {'role': 'user', 'content': [{'type': 'text', 'text': '(conversation continues)'}]})
    # The request has to end on a user turn; prefilled replies are rejected.
    if out[-1]['role'] == 'assistant':
        out.append({'role': 'user', 'content': [{'type': 'text', 'text': 'Continue.'}]})
    return '\n\n'.join(system_parts), out


def build_params(
    llm: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, effort: str = '',
) -> dict[str, Any]:
    system, converted = convert_messages(messages)
    params: dict[str, Any] = {
        'model': llm.model,
        'max_tokens': int(llm.max_tokens or 8192),
        'messages': converted,
        # Follows the end of the conversation, so each step of a tool loop
        # reads everything before it from cache.
        'cache_control': dict(_CACHE),
    }
    if system:
        # Tools render before the system prompt: this one marker covers both.
        params['system'] = [{'type': 'text', 'text': system, 'cache_control': dict(_CACHE)}]
    native_tools = convert_tools(tools)
    if native_tools:
        params['tools'] = native_tools
    version = _version(llm.model)
    if version is not None and version >= (4, 6):
        # 'summarized' so the chat window can show reasoning as it streams.
        params['thinking'] = {'type': 'adaptive', 'display': 'summarized'}
        mapped = _EFFORTS.get(effort)
        if mapped == 'xhigh' and version < (4, 7):
            mapped = 'high'
        if mapped:
            params['output_config'] = {'effort': mapped}
    # No temperature: the current Messages API takes no sampling parameters.
    return params


def _usage(start: Any, output_tokens: int | None) -> dict[str, int]:
    read = int(getattr(start, 'cache_read_input_tokens', 0) or 0)
    written = int(getattr(start, 'cache_creation_input_tokens', 0) or 0)
    fresh = int(getattr(start, 'input_tokens', 0) or 0)
    return {
        # Everything the model read, cached or not, as other providers report it.
        'prompt_tokens': fresh + read + written,
        'completion_tokens': int(output_tokens if output_tokens is not None else getattr(start, 'output_tokens', 0) or 0),
        'cached_tokens': read,
        'cache_write_tokens': written,
    }


# --- calling -----------------------------------------------------------------

async def _create(client: Any, params: dict[str, Any]):
    """messages.create with the same retry and budget rules as the OpenAI path."""
    import anthropic

    from cognitrix.providers import base
    from cognitrix.tasks.accounting import current_task_accounting

    accounting = current_task_accounting()
    attempt = 0
    while True:
        attempt += 1
        if attempt > 1 and accounting is not None:
            retry_output_tokens = await accounting.begin_provider_retry()
            if retry_output_tokens is not None:
                params = {**params, 'max_tokens': retry_output_tokens}
        try:
            return await client.messages.create(**params)
        except (anthropic.RateLimitError, anthropic.APITimeoutError,
                anthropic.APIConnectionError, anthropic.InternalServerError) as e:
            if accounting is not None:
                await accounting.finish_failed_provider_attempt()
            limit = base._MAX_RATE_LIMIT_ATTEMPTS if isinstance(e, anthropic.RateLimitError) else base._MAX_ATTEMPTS
            if attempt >= limit:
                raise
            delay = base._retry_delay(e, attempt)
            logger.warning('Transient Anthropic error (attempt %s, retrying in %.1fs): %s', attempt, delay, e)
            if accounting is not None:
                await accounting.wait_within_wall(asyncio.sleep(delay))
            else:
                await asyncio.sleep(delay)


def _finish_tool_calls(calls: dict[int, dict[str, Any]], truncated: bool) -> list[dict[str, Any]]:
    from cognitrix.providers.base import _malformed_arguments_error

    parsed = []
    for _idx, call in sorted(calls.items()):
        raw = call.get('json') or '{}'
        item: dict[str, Any] = {'name': call['name'], 'arguments': {}, 'tool_call_id': call['id']}
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(args, dict):
                raise ValueError('arguments are not an object')
            item['arguments'] = args
        except ValueError:
            logger.warning("Tool call '%s' had malformed JSON arguments. Raw: %r", call['name'], raw)
            item['arguments_error'] = _malformed_arguments_error(call['name'], raw, truncated)
        parsed.append(item)
    return parsed


def _refusal_text(message_delta: Any) -> str:
    details = getattr(message_delta, 'stop_details', None)
    explanation = getattr(details, 'explanation', None)
    return explanation or 'The model declined to answer this request.'


Fallback = Callable[[], Awaitable[Any]]


async def respond(client: Any, params: dict[str, Any], fallback: Fallback | None = None) -> LLMResponse:
    """One non-streaming reply."""
    import anthropic

    from cognitrix.providers.base import record_usage

    try:
        try:
            message = await _create(client, params)
        except anthropic.BadRequestError as e:
            if fallback is None:
                raise
            logger.warning('Anthropic rejected the native request, using the compatible endpoint: %s', e)
            return await fallback()
        response = LLMResponse()
        calls: dict[int, dict[str, Any]] = {}
        reasoning = ''
        text = ''
        for i, block in enumerate(message.content):
            if block.type == 'text':
                text += block.text
            elif block.type == 'thinking':
                reasoning += block.thinking or ''
            elif block.type == 'tool_use':
                calls[i] = {'id': block.id, 'name': block.name, 'json': block.input}
        if message.stop_reason == 'refusal' and not text:
            text = _refusal_text(message)
        response.finish_reason = _STOP_REASONS.get(message.stop_reason or '', message.stop_reason)
        if reasoning:
            response.add_reasoning_chunk(reasoning)
        response.add_chunk(text)
        if calls:
            response.tool_calls = _finish_tool_calls(calls, message.stop_reason == 'max_tokens')
        response.usage = _usage(message.usage, None)
        record_usage(response.usage)
        return response
    except ExecutionControlError:
        raise
    except Exception as e:
        logger.exception('Error in Anthropic response: %s', e)
        msg = f'Response error: {e}'
        return LLMResponse(llm_response=msg, error=msg)


async def stream(
    client: Any, params: dict[str, Any], fallback: Callable[[], AsyncIterator[LLMResponse]] | None = None,
) -> AsyncIterator[LLMResponse]:
    """A streamed reply, yielded the way base.py's OpenAI stream handler does."""
    import anthropic

    from cognitrix.providers.base import record_usage

    try:
        try:
            events = await _create(client, {**params, 'stream': True})
        except anthropic.BadRequestError as e:
            if fallback is None:
                raise
            logger.warning('Anthropic rejected the native request, using the compatible endpoint: %s', e)
            async for item in fallback():
                yield item
            return
        response = LLMResponse()
        in_reasoning = False
        calls: dict[int, dict[str, Any]] = {}
        start_usage: Any = None
        output_tokens: int | None = None
        stop_reason: str | None = None
        last_delta: Any = None
        async for event in events:
            kind = event.type
            if kind == 'message_start':
                start_usage = event.message.usage
            elif kind == 'content_block_start':
                block = event.content_block
                if block.type == 'tool_use':
                    calls[event.index] = {'id': block.id, 'name': block.name, 'json': ''}
            elif kind == 'content_block_delta':
                delta = event.delta
                if delta.type == 'thinking_delta' and delta.thinking:
                    response.add_reasoning_chunk(delta.thinking)
                    response.current_chunk = delta.thinking if in_reasoning else '<think>' + delta.thinking
                    in_reasoning = True
                    yield response
                elif delta.type == 'text_delta' and delta.text:
                    chunk = delta.text
                    response.add_chunk(chunk)
                    response.current_chunk = ('\n</think>\n' + chunk) if in_reasoning else chunk
                    in_reasoning = False
                    yield response
                elif delta.type == 'input_json_delta' and event.index in calls:
                    calls[event.index]['json'] += delta.partial_json or ''
            elif kind == 'message_delta':
                last_delta = event.delta
                stop_reason = event.delta.stop_reason or stop_reason
                output_tokens = getattr(event.usage, 'output_tokens', output_tokens)
        closing = '\n</think>\n' if in_reasoning else ''
        if stop_reason == 'refusal' and not response.llm_response:
            notice = _refusal_text(last_delta)
            response.add_chunk(notice)
            closing += notice
        if calls:
            response.tool_calls = _finish_tool_calls(calls, stop_reason == 'max_tokens')
        response.finish_reason = _STOP_REASONS.get(stop_reason or '', stop_reason)
        if start_usage is not None:
            response.usage = _usage(start_usage, output_tokens)
            record_usage(response.usage)
        # The last yield carries tool calls and totals; no repeated text.
        response.current_chunk = closing
        yield response
    except ExecutionControlError:
        raise
    except Exception as e:
        logger.exception('Error in Anthropic streaming response: %s', e)
        msg = f'Streaming error: {e}'
        err = LLMResponse(llm_response=msg, error=msg)
        err.current_chunk = msg
        yield err
