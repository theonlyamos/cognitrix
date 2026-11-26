"""Speak to the agent in the terminal (`cognitrix --audio`, `/voice`).

Records from the default microphone until Enter, then turns the recording
into text with the same speech-to-text providers as the app's voice input
(cognitrix/providers/transcription.py), using whichever has a saved key.
Recording uses the optional ``sounddevice`` package (pip install sounddevice).
"""
from __future__ import annotations

import asyncio
import io
import sys
import wave

SAMPLE_RATE = 16_000
PREFERRED = ('deepgram', 'openai', 'groq', 'google')


class VoiceUnavailable(Exception):
    """Voice input can't work here; the message says why and what to do."""


def pick_providers() -> list[tuple[str, str]]:
    """(provider, key) for each speech-to-text provider with a saved key, best first."""
    from cognitrix.providers.transcription import server_key

    found = [(p, server_key(p)) for p in PREFERRED]
    found = [(p, k) for p, k in found if k]
    if found:
        return found
    raise VoiceUnavailable(
        'No speech-to-text key saved. Save one with `cognitrix keys set deepgram` (or openai, groq, google).')


def wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def _record_until_enter() -> bytes:
    try:
        import sounddevice  # type: ignore[import-not-found]
    except Exception as exc:
        raise VoiceUnavailable(
            'Recording needs the sounddevice package: pip install sounddevice') from exc
    chunks: list[bytes] = []

    def collect(data, frames, time, status):  # noqa: ARG001
        chunks.append(bytes(data))

    try:
        with sounddevice.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', callback=collect):
            input()
    except VoiceUnavailable:
        raise
    except Exception as exc:
        raise VoiceUnavailable(f"Couldn't record from the microphone: {exc}") from exc
    return b''.join(chunks)


async def listen() -> str:
    """Record until Enter and return what was said ('' for nothing)."""
    from cognitrix.providers.transcription import TranscriptionError, transcribe

    providers = pick_providers()
    print('🎙  Listening... press Enter when you are done.', file=sys.stderr, flush=True)
    pcm = await asyncio.to_thread(_record_until_enter)
    if len(pcm) < SAMPLE_RATE // 2:  # under a quarter second
        return ''
    audio = wav_bytes(pcm)
    problem = ''
    for provider, key in providers:  # out of credit or down: try the next one
        try:
            return (await transcribe(audio, mime='audio/wav', provider=provider, model='', api_key=key)).strip()
        except TranscriptionError as exc:
            problem = str(exc)
    raise VoiceUnavailable(problem or "Couldn't turn the recording into text.")
