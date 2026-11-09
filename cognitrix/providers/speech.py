"""Text-to-speech for spoken replies.

OpenAI's ``/audio/speech`` returns MP3. Gemini's speech models return raw
24 kHz PCM, which is wrapped in a WAV header so a browser can play it. Keys
are the same ones voice input uses.
"""
from __future__ import annotations

import base64
import logging
import os
import struct
from dataclasses import dataclass

import httpx

from .base import _DEFAULT_BASE_URLS, assert_key_destination_allowed

logger = logging.getLogger('cognitrix.log')

# One request reads at most this much; the app sends longer replies in pieces.
MAX_SPEECH_CHARS = 4000

GEMINI_SPEECH_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


@dataclass(frozen=True)
class SpeechProvider:
    id: str
    label: str
    model_env: str
    default_model: str
    voices: tuple[str, ...]

    @property
    def model(self) -> str:
        return os.getenv(self.model_env, '').strip() or self.default_model


SPEECH_PROVIDERS: dict[str, SpeechProvider] = {
    p.id: p
    for p in (
        SpeechProvider(
            'openai', 'OpenAI', 'COGNITRIX_OPENAI_SPEECH_MODEL', 'gpt-4o-mini-tts',
            ('alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'nova', 'onyx', 'sage', 'shimmer', 'verse'),
        ),
        SpeechProvider(
            'google', 'Google Gemini', 'COGNITRIX_GEMINI_SPEECH_MODEL', 'gemini-3.8-flash-tts',
            ('Kore', 'Puck', 'Charon', 'Fenrir', 'Aoede', 'Leda', 'Orus', 'Zephyr'),
        ),
    )
}


class SpeechError(Exception):
    """A failure worth showing the user as is."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def pcm_to_wav(pcm: bytes, sample_rate: int = 24000, channels: int = 1, bits: int = 16) -> bytes:
    block = channels * (bits // 8)
    header = b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVE'
    header += b'fmt ' + struct.pack('<IHHIIHH', 16, 1, channels, sample_rate, sample_rate * block, block, bits)
    header += b'data' + struct.pack('<I', len(pcm))
    return header + pcm


def _rate_from_mime(mime: str) -> int:
    for part in (mime or '').split(';'):
        name, _, value = part.strip().partition('=')
        if name == 'rate' and value.isdigit():
            return int(value)
    return 24000


def _friendly(provider: str, status: int | None) -> SpeechError:
    label = SPEECH_PROVIDERS[provider].label
    if status in (401, 403):
        return SpeechError(f'{label} rejected the API key. Check it under Settings, Voice.', 400)
    if status == 402:
        return SpeechError(f'Your {label} account is out of credit.', 400)
    if status == 404:
        return SpeechError(f"{label} doesn't know that speech model.", 400)
    if status == 429:
        return SpeechError(f'{label} is rate limiting requests. Try again in a moment.', 429)
    return SpeechError(f"{label} couldn't read that aloud. Try again.")


async def _openai(text: str, voice: str, model: str, api_key: str) -> tuple[bytes, str]:
    base_url = (os.getenv('OPENAI_BASE_URL') or _DEFAULT_BASE_URLS.get('openai', '')).rstrip('/')
    assert_key_destination_allowed(base_url, api_key)
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f'{base_url}/audio/speech',
            headers={'Authorization': f'Bearer {api_key}'},
            json={'model': model, 'voice': voice, 'input': text, 'response_format': 'mp3'},
        )
        response.raise_for_status()
        return response.content, 'audio/mpeg'


async def _gemini(text: str, voice: str, model: str, api_key: str) -> tuple[bytes, str]:
    body = {
        'contents': [{'parts': [{'text': text}]}],
        'generationConfig': {
            'responseModalities': ['AUDIO'],
            'speechConfig': {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': voice}}},
        },
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            GEMINI_SPEECH_URL.format(model=model), headers={'x-goog-api-key': api_key}, json=body,
        )
        response.raise_for_status()
        data = response.json()
    try:
        part = next(p['inlineData'] for p in data['candidates'][0]['content']['parts'] if 'inlineData' in p)
    except (KeyError, IndexError, StopIteration, TypeError) as exc:
        raise SpeechError("Google Gemini didn't return any audio. Try again.") from exc
    pcm = base64.b64decode(part.get('data') or '')
    return pcm_to_wav(pcm, _rate_from_mime(part.get('mimeType', ''))), 'audio/wav'


async def speak(text: str, *, provider: str, voice: str = '', api_key: str) -> tuple[bytes, str]:
    """Read ``text`` aloud with ``provider``. Returns (audio, mime type)."""
    spec = SPEECH_PROVIDERS.get(provider)
    if spec is None:
        raise SpeechError(f"Unknown speech provider '{provider}'.", 400)
    text = (text or '').strip()
    if not text:
        raise SpeechError('There is nothing to read.', 400)
    if len(text) > MAX_SPEECH_CHARS:
        raise SpeechError(f'That is too long to read in one go ({MAX_SPEECH_CHARS} characters at most).', 413)
    if not api_key:
        raise SpeechError(f'No {spec.label} API key. Add one under Settings, Voice.', 400)
    voice = voice if voice in spec.voices else spec.voices[0]
    try:
        if provider == 'openai':
            return await _openai(text, voice, spec.model, api_key)
        return await _gemini(text, voice, spec.model, api_key)
    except SpeechError:
        raise
    except ValueError as exc:  # untrusted base URL for a server key
        raise SpeechError(str(exc), 400) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning('Speech with %s failed: %s %s', provider, exc.response.status_code, exc.response.text[:300])
        raise _friendly(provider, exc.response.status_code) from exc
    except Exception as exc:
        logger.warning('Speech with %s failed: %s', provider, exc)
        raise _friendly(provider, None) from exc


__all__ = ['MAX_SPEECH_CHARS', 'SPEECH_PROVIDERS', 'SpeechError', 'SpeechProvider', 'pcm_to_wav', 'speak']
