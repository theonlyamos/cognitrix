"""Speech-to-text for the chat box's voice input.

The browser records a short clip (16 kHz mono WAV) and the engine sends it to
one of these providers. OpenAI and Groq share the OpenAI ``/audio/
transcriptions`` endpoint; Gemini gets the audio inline through Google's
OpenAI-compatible chat endpoint; Deepgram has its own REST API.

Audio is only held in memory for the length of the request.
"""
from __future__ import annotations

import base64
import logging
import os
import struct
from dataclasses import dataclass

import httpx

from .base import _DEFAULT_BASE_URLS, _get_or_create_client, assert_key_destination_allowed

logger = logging.getLogger('cognitrix.log')

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_AUDIO_SECONDS = 10 * 60
# Gemini caps an inline request at about 20 MB and base64 adds a third, so
# longer clips go up in pieces.
GEMINI_CHUNK_SECONDS = 4 * 60

DEEPGRAM_URL = 'https://api.deepgram.com/v1/listen'

GEMINI_PROMPT = (
    'Transcribe this audio verbatim. Reply with only the words spoken, in the '
    'language they were spoken in, with normal punctuation. Do not summarise, '
    'translate, answer questions in it or add any notes. If nobody speaks, '
    'reply with nothing.'
)


@dataclass(frozen=True)
class VoiceProvider:
    id: str
    label: str
    models: tuple[str, ...]
    key_env: str
    key_url: str

    @property
    def default_model(self) -> str:
        return self.models[0]


# Order is the "Auto" preference when the user hasn't picked one.
VOICE_PROVIDERS: dict[str, VoiceProvider] = {
    p.id: p
    for p in (
        VoiceProvider(
            'openai', 'OpenAI',
            ('gpt-transcribe', 'gpt-4o-mini-transcribe', 'whisper-1'),
            'OPENAI_API_KEY', 'https://platform.openai.com/api-keys',
        ),
        VoiceProvider(
            'groq', 'Groq',
            ('whisper-large-v3-turbo', 'whisper-large-v3'),
            'GROQ_API_KEY', 'https://console.groq.com/keys',
        ),
        VoiceProvider(
            'google', 'Google Gemini',
            ('gemini-3.5-flash', 'gemini-3.1-flash', 'gemini-3.1-pro'),
            'GOOGLE_API_KEY', 'https://aistudio.google.com/apikey',
        ),
        VoiceProvider(
            'deepgram', 'Deepgram',
            ('nova-3', 'nova-2'),
            'DEEPGRAM_API_KEY', 'https://console.deepgram.com/',
        ),
    )
}


class TranscriptionError(Exception):
    """A failure worth showing the user as is."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def server_key(provider: str) -> str:
    """The engine's own key for ``provider`` (environment or settings)."""
    from cognitrix.config import settings

    spec = VOICE_PROVIDERS.get(provider)
    if spec is None:
        return ''
    value = os.getenv(spec.key_env, '')
    if not value:
        try:
            value = settings.get_api_key(provider) or ''
        except Exception:
            value = ''
    return value.strip()


def _base_url(provider: str) -> str:
    return (os.getenv(f'{provider.upper()}_BASE_URL') or _DEFAULT_BASE_URLS.get(provider, '')).rstrip('/')


# ---------------------------------------------------------------- WAV helpers

@dataclass
class WavInfo:
    sample_rate: int
    channels: int
    bits: int
    data_offset: int
    data_size: int

    @property
    def seconds(self) -> float:
        frame = self.channels * (self.bits // 8)
        return self.data_size / (self.sample_rate * frame) if frame and self.sample_rate else 0.0


def parse_wav(audio: bytes) -> WavInfo | None:
    """Read a PCM WAV header; None when ``audio`` isn't one."""
    if len(audio) < 12 or audio[:4] != b'RIFF' or audio[8:12] != b'WAVE':
        return None
    pos = 12
    fmt = None
    while pos + 8 <= len(audio):
        chunk_id = audio[pos:pos + 4]
        size = struct.unpack('<I', audio[pos + 4:pos + 8])[0]
        body = pos + 8
        if chunk_id == b'fmt ' and size >= 16:
            _, channels, rate, _, _, bits = struct.unpack('<HHIIHH', audio[body:body + 16])
            fmt = (rate, channels, bits)
        elif chunk_id == b'data' and fmt:
            data_size = min(size, len(audio) - body)
            return WavInfo(fmt[0], fmt[1], fmt[2], body, data_size)
        pos = body + size + (size & 1)
    return None


def _wav_bytes(pcm: bytes, info: WavInfo) -> bytes:
    block = info.channels * (info.bits // 8)
    header = b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVE'
    header += b'fmt ' + struct.pack(
        '<IHHIIHH', 16, 1, info.channels, info.sample_rate, info.sample_rate * block, block, info.bits
    )
    header += b'data' + struct.pack('<I', len(pcm))
    return header + pcm


def split_wav(audio: bytes, info: WavInfo, seconds: int) -> list[bytes]:
    """Cut a PCM WAV into pieces of at most ``seconds`` each."""
    block = info.channels * (info.bits // 8)
    step = max(block, info.sample_rate * block * seconds)
    pcm = audio[info.data_offset:info.data_offset + info.data_size]
    return [_wav_bytes(pcm[i:i + step], info) for i in range(0, len(pcm), step)] or [audio]


def check_audio(audio: bytes) -> WavInfo | None:
    if not audio:
        raise TranscriptionError('The recording was empty.', 400)
    if len(audio) > MAX_AUDIO_BYTES:
        raise TranscriptionError('The recording is too large (25 MB at most).', 413)
    info = parse_wav(audio)
    # A little slack over the cap for the recorder's own stop latency.
    if info and info.seconds > MAX_AUDIO_SECONDS + 5:
        raise TranscriptionError('Recordings can be 10 minutes at most.', 413)
    return info


# ------------------------------------------------------------------ providers

def _extension(mime: str) -> str:
    mime = (mime or '').split(';')[0].strip().lower()
    return {
        'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/wave': 'wav',
        'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mpeg': 'mp3',
        'audio/mp4': 'm4a', 'audio/m4a': 'm4a', 'audio/x-m4a': 'm4a',
    }.get(mime, 'wav')


def _friendly(provider: str, exc: Exception) -> TranscriptionError:
    status = getattr(exc, 'status_code', None) or getattr(getattr(exc, 'response', None), 'status_code', None)
    label = VOICE_PROVIDERS[provider].label
    if status in (401, 403):
        return TranscriptionError(f'{label} rejected the API key. Check it under Settings, Voice input.', 400)
    if status == 402:
        return TranscriptionError(f'Your {label} account is out of credit.', 400)
    if status == 404:
        return TranscriptionError(f'{label} doesn\'t know that model. Pick another under Settings, Voice input.', 400)
    if status == 429:
        return TranscriptionError(f'{label} is rate limiting requests. Try again in a moment.', 429)
    if status == 413:
        return TranscriptionError(f'{label} says the recording is too large.', 413)
    logger.warning('Transcription with %s failed: %s', provider, exc)
    return TranscriptionError(f'{label} couldn\'t transcribe the recording. Try again.')


async def _openai_style(provider: str, audio: bytes, mime: str, model: str, api_key: str, language: str | None) -> str:
    base_url = _base_url(provider)
    assert_key_destination_allowed(base_url, api_key)
    client = _get_or_create_client(base_url, api_key)
    kwargs: dict = {'model': model, 'file': (f'recording.{_extension(mime)}', audio, mime or 'audio/wav')}
    if language:
        kwargs['language'] = language
    result = await client.audio.transcriptions.create(**kwargs)
    return str(getattr(result, 'text', result) or '')


async def _gemini(audio: bytes, info: WavInfo | None, model: str, api_key: str, language: str | None) -> str:
    if info is None:
        raise TranscriptionError('Gemini needs the recording as WAV audio.', 400)
    base_url = _base_url('google')
    assert_key_destination_allowed(base_url, api_key)
    client = _get_or_create_client(base_url, api_key)
    prompt = GEMINI_PROMPT + (f' The speaker uses the language with code "{language}".' if language else '')
    parts: list[str] = []
    for piece in split_wav(audio, info, GEMINI_CHUNK_SECONDS):
        result = await client.chat.completions.create(
            model=model,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'input_audio', 'input_audio': {
                        'data': base64.b64encode(piece).decode('ascii'), 'format': 'wav',
                    }},
                ],
            }],
        )
        text = (result.choices[0].message.content or '').strip() if result.choices else ''
        if text:
            parts.append(text)
    return ' '.join(parts)


async def _deepgram(audio: bytes, mime: str, model: str, api_key: str, language: str | None) -> str:
    params = {'model': model, 'smart_format': 'true', 'punctuate': 'true'}
    if language:
        params['language'] = language
    else:
        params['detect_language'] = 'true'
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            DEEPGRAM_URL,
            params=params,
            content=audio,
            headers={'Authorization': f'Token {api_key}', 'Content-Type': mime or 'audio/wav'},
        )
        response.raise_for_status()
        data = response.json()
    try:
        return data['results']['channels'][0]['alternatives'][0]['transcript'] or ''
    except (KeyError, IndexError, TypeError):
        return ''


async def transcribe(
    audio: bytes,
    *,
    mime: str,
    provider: str,
    model: str,
    api_key: str,
    language: str | None = None,
) -> str:
    """Turn a recording into text with ``provider``. Raises TranscriptionError."""
    if provider not in VOICE_PROVIDERS:
        raise TranscriptionError(f"Unknown voice provider '{provider}'.", 400)
    if not api_key:
        label = VOICE_PROVIDERS[provider].label
        raise TranscriptionError(f'No {label} API key. Add one under Settings, Voice input.', 400)
    info = check_audio(audio)
    model = model or VOICE_PROVIDERS[provider].default_model
    language = (language or '').strip().lower()[:12] or None
    try:
        if provider in ('openai', 'groq'):
            text = await _openai_style(provider, audio, mime, model, api_key, language)
        elif provider == 'google':
            text = await _gemini(audio, info, model, api_key, language)
        else:
            text = await _deepgram(audio, mime, model, api_key, language)
    except TranscriptionError:
        raise
    except ValueError as exc:  # untrusted base URL for a server key
        raise TranscriptionError(str(exc), 400) from exc
    except Exception as exc:
        raise _friendly(provider, exc) from exc
    return ' '.join(text.split())


__all__ = [
    'MAX_AUDIO_BYTES', 'MAX_AUDIO_SECONDS', 'VOICE_PROVIDERS', 'TranscriptionError',
    'VoiceProvider', 'check_audio', 'parse_wav', 'server_key', 'split_wav', 'transcribe',
]
