"""Voice input: per-user speech-to-text settings and the transcription call.

``GET /audio/settings`` lists the providers, which of them already have a key
on the server, and the user's choice. ``PUT /audio/settings`` saves the
provider, model and (when the server has no key for it) the user's own key.
``POST /audio/transcriptions`` takes the raw recording as the request body and
returns ``{"text": ...}``.

Keys are never sent back; the settings only say whether one is set.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from cognitrix.agents import access as agent_access
from cognitrix.common.secret_box import seal, unseal_or_empty
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.models.voice_settings import VoiceSettings

from ...providers.transcription import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    VOICE_PROVIDERS,
    TranscriptionError,
    server_key,
    transcribe,
)

logger = logging.getLogger('cognitrix.log')

audio_api = APIRouter(prefix='/audio', dependencies=[Depends(jwt_only)])

MAX_MODEL_LENGTH = 100
MAX_KEY_LENGTH = 400
# Transcriptions per user per minute; generous for a person, stops a runaway loop.
RATE_LIMIT_PER_MINUTE = 20
_recent: dict[str, deque[float]] = defaultdict(deque)


class VoiceSettingsUpdate(BaseModel):
    provider: str | None = None
    """'' means Auto: the first provider with a key."""
    model: str | None = None
    api_key: str | None = None
    """A key for ``provider``. '' removes the saved one."""


def _user_key(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


async def _load(user_key: str) -> VoiceSettings | None:
    return await VoiceSettings.find_one({'user_id': user_key})


def _keys(row: VoiceSettings | None) -> dict[str, str]:
    if row is None or not row.api_keys:
        return {}
    try:
        data = json.loads(unseal_or_empty(row.api_keys) or '{}')
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items() if v} if isinstance(data, dict) else {}


def _effective(row: VoiceSettings | None) -> tuple[str, str, str]:
    """(provider, model, api_key) to use for this user; provider '' if none works."""
    keys = _keys(row)
    chosen = (row.provider if row else '') or ''
    if chosen in VOICE_PROVIDERS:
        provider = chosen
    else:
        provider = next((p for p in VOICE_PROVIDERS if keys.get(p) or server_key(p)), '')
    if not provider:
        return '', '', ''
    model = (row.model if row and chosen == provider else '') or VOICE_PROVIDERS[provider].default_model
    return provider, model, keys.get(provider) or server_key(provider)


def _payload(row: VoiceSettings | None) -> dict[str, Any]:
    keys = _keys(row)
    provider, model, api_key = _effective(row)
    return {
        'provider': (row.provider if row else '') or '',
        'model': (row.model if row else '') or '',
        'effective': {'provider': provider, 'model': model, 'ready': bool(provider and api_key)},
        'max_seconds': MAX_AUDIO_SECONDS,
        'providers': [
            {
                'id': p.id,
                'label': p.label,
                'models': list(p.models),
                'default_model': p.default_model,
                'server_key': bool(server_key(p.id)),
                'user_key': bool(keys.get(p.id)),
                'key_hint': f'…{keys[p.id][-4:]}' if keys.get(p.id) else None,
                'key_url': p.key_url,
            }
            for p in VOICE_PROVIDERS.values()
        ],
    }


@audio_api.get('/settings')
async def get_voice_settings(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    return _payload(await _load(_user_key(ctx)))


@audio_api.put('/settings')
async def update_voice_settings(
    body: VoiceSettingsUpdate, ctx: AuthContext = Depends(get_auth_context)
) -> dict[str, Any]:
    user_key = _user_key(ctx)
    row = await _load(user_key)
    provider = (row.provider if row else '') or ''
    model = (row.model if row else '') or ''
    keys = _keys(row)

    if body.provider is not None:
        new_provider = body.provider.strip().lower()
        if new_provider and new_provider not in VOICE_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unknown voice provider '{body.provider}'.")
        if new_provider != provider:
            model = ''
        provider = new_provider
    if body.model is not None:
        model = body.model.strip()
        if len(model) > MAX_MODEL_LENGTH or any(c.isspace() for c in model):
            raise HTTPException(status_code=400, detail='That model name looks wrong.')
        if model and not provider:
            raise HTTPException(status_code=400, detail='Pick a provider before choosing a model.')
    if body.api_key is not None:
        if not provider:
            raise HTTPException(status_code=400, detail='Pick a provider before adding a key.')
        key = body.api_key.strip()
        if len(key) > MAX_KEY_LENGTH or any(c.isspace() for c in key):
            raise HTTPException(status_code=400, detail='That API key looks wrong.')
        if key:
            keys[provider] = key
        else:
            keys.pop(provider, None)

    data = {'provider': provider, 'model': model, 'api_keys': seal(json.dumps(keys)) if keys else ''}
    if row is None:
        row = VoiceSettings(user_id=user_key, **data)
        await row.save()
    else:
        await VoiceSettings.update_one({'user_id': user_key}, data)
    return _payload(await _load(user_key))


def _check_rate(user_key: str) -> None:
    now = time.monotonic()
    recent = _recent[user_key]
    while recent and now - recent[0] > 60:
        recent.popleft()
    if len(recent) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail='Too many transcriptions. Wait a minute and try again.')
    recent.append(now)


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get('content-length')
    if declared and declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail='The recording is too large (25 MB at most).')
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail='The recording is too large (25 MB at most).')
        chunks.append(chunk)
    return b''.join(chunks)


@audio_api.post('/transcriptions')
async def create_transcription(
    request: Request,
    language: str | None = None,
    ctx: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Transcribe the request body (16 kHz mono WAV from the app's recorder;
    OpenAI, Groq and Deepgram also take webm/ogg/mp3/m4a)."""
    user_key = _user_key(ctx)
    mime = (request.headers.get('content-type') or 'audio/wav').split(';')[0].strip().lower()
    if not mime.startswith('audio/'):
        raise HTTPException(status_code=415, detail='Send the recording as audio (for example audio/wav).')
    provider, model, api_key = _effective(await _load(user_key))
    if not provider:
        raise HTTPException(
            status_code=400,
            detail='Voice input needs a speech-to-text provider. Add a key under Settings, Voice input.',
        )
    audio = await _read_body(request)
    _check_rate(user_key)
    try:
        text = await transcribe(audio, mime=mime, provider=provider, model=model, api_key=api_key, language=language)
    except TranscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {'text': text, 'provider': provider, 'model': model}
