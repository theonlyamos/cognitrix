"""Sound for each desktop, so an agent can sit in a call from the sandbox browser.

Every desktop gets its own pair of PulseAudio devices when it starts
(``DesktopAudio.setup``):

- a speaker, ``cx_<desktop>_out``: what the desktop's apps play (the people
  in a call). Its monitor is what ``Recorder`` listens to.
- a microphone, ``cx_<desktop>_mic``: silent, except while the agent speaks
  (``say`` plays audio into ``cx_<desktop>_feed``, whose monitor the mic is).

Apps on the desktop find them through ``PULSE_SINK`` and ``PULSE_SOURCE``,
which the supervisor puts in the desktop's environment. Nothing here touches
another desktop's devices, so two agents in two calls don't hear each other.

``Recorder`` cuts what it hears into speech-sized pieces: it starts a piece
on sound, ends it after a short pause (or at ``MAX_PIECE_S``), and drops
stretches of silence, so the engine only transcribes what was said.

Standard library only: the image ships just ``cognitrix.computer``.
"""
from __future__ import annotations

import base64
import io
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from array import array
from collections import deque

logger = logging.getLogger('cognitrix.sandbox.audio')

RATE = 16000  # what every speech-to-text provider takes
FRAME_S = 0.1
FRAME_BYTES = int(RATE * FRAME_S) * 2  # 16-bit mono
SPEECH_RMS = int(os.environ.get('COGNITRIX_SANDBOX_SPEECH_RMS', '350'))
"""Loudness (16-bit RMS) above which a 100 ms frame counts as sound."""
PAUSE_S = 0.9  # a pause this long ends a piece
MIN_SOUND_S = 0.4  # a piece needs this much sound, else it was a click or a cough
MAX_PIECE_S = 20.0
KEEP_PIECES = 60  # what waits for the engine at most (the oldest go first)


def pulse_ready() -> bool:
    return bool(shutil.which('pactl') and shutil.which('parec'))


def device_names(desktop: str) -> dict[str, str]:
    base = f'cx_{desktop}'.replace('-', '_')
    return {'speaker': f'{base}_out', 'feed': f'{base}_feed', 'mic': f'{base}_mic'}


def _pactl(*args: str) -> str:
    out = subprocess.run(['pactl', *args], capture_output=True, text=True, timeout=10)  # noqa: S603, S607
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f'pactl {args[0]} failed')
    return out.stdout.strip()


class DesktopAudio:
    """One desktop's speaker and microphone, and what listens to them."""

    def __init__(self, desktop: str):
        self.desktop = desktop
        self.names = device_names(desktop)
        self.modules: list[str] = []
        self.recorder: Recorder | None = None
        self._say_guard = threading.Lock()

    def setup(self) -> dict[str, str]:
        """Make the devices; returns the environment the desktop's apps get."""
        if not pulse_ready():
            return {}
        try:
            n = self.names
            self.modules.append(_pactl('load-module', 'module-null-sink', f'sink_name={n["speaker"]}',
                                       f'sink_properties=device.description=Speaker-{self.desktop}'))
            self.modules.append(_pactl('load-module', 'module-null-sink', f'sink_name={n["feed"]}',
                                       f'sink_properties=device.description=Voice-{self.desktop}'))
            self.modules.append(_pactl('load-module', 'module-remap-source', f'master={n["feed"]}.monitor',
                                       f'source_name={n["mic"]}',
                                       f'source_properties=device.description=Microphone-{self.desktop}'))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            logger.warning('desktop %s: no sound devices (%s)', self.desktop, exc)
            self.teardown()
            return {}
        return {'PULSE_SINK': n['speaker'], 'PULSE_SOURCE': n['mic']}

    def teardown(self) -> None:
        self.stop()
        for module in reversed(self.modules):
            try:
                _pactl('unload-module', module)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
        self.modules = []

    @property
    def ready(self) -> bool:
        return bool(self.modules)

    # -------------------------------------------------------------- listening

    def start(self) -> dict:
        if not self.ready:
            return {'ok': False, 'error': 'no_audio', 'message': "This computer has no sound. Update it under "
                    'Settings, Computer to join calls.'}
        if self.recorder is None or not self.recorder.alive:
            self.recorder = Recorder(self.names['speaker'] + '.monitor')
            self.recorder.start()
        return {'ok': True, 'listening': True}

    def stop(self, after: int | None = None) -> dict:
        """Stop capture, optionally collecting unread speech including the final utterance."""
        result = {'ok': True, 'listening': False}
        rec = self.recorder
        if rec is not None:
            rec.stop()
            self.recorder = None
        if after is not None:
            result['pieces'] = rec.pieces_after(after) if rec is not None else []
        return result

    def read(self, after: int, ack: int | None = None) -> dict:
        rec = self.recorder
        if rec is None:
            return {'ok': True, 'listening': False, 'pieces': [], 'last_sound': 0.0}
        # quiet_s is measured here, so the engine on another machine needs no matching clock.
        quiet = round(time.time() - rec.last_sound, 2) if rec.last_sound else None
        return {'ok': True, 'listening': rec.alive, 'pieces': rec.pieces_after(after, ack),
                'last_sound': rec.last_sound, 'quiet_s': quiet, 'error': rec.error}

    # --------------------------------------------------------------- speaking

    def say(self, audio: bytes, suffix: str = '.wav') -> dict:
        """Play ``audio`` into the desktop's microphone, waiting until it ends."""
        if not self.ready:
            return {'ok': False, 'error': 'no_audio', 'message': 'This computer has no sound.'}
        if not audio:
            return {'ok': False, 'error': 'bad_request', 'message': 'No audio.'}
        suffix = suffix if suffix in ('.wav', '.mp3', '.ogg', '.opus', '.webm', '.m4a') else '.wav'
        with self._say_guard, tempfile.TemporaryDirectory(prefix='cx-say-') as tmp:
            source = os.path.join(tmp, 'in' + suffix)
            with open(source, 'wb') as fh:
                fh.write(audio)
            playable = source
            if suffix != '.wav' or not _plain_wav(audio):
                ffmpeg = shutil.which('ffmpeg')
                if not ffmpeg:
                    return {'ok': False, 'error': 'no_ffmpeg', 'message': 'Only WAV audio can be played here.'}
                playable = os.path.join(tmp, 'out.wav')
                done = subprocess.run([ffmpeg, '-nostdin', '-loglevel', 'error', '-y', '-i', source,  # noqa: S603
                                       '-ac', '1', '-ar', '48000', playable], capture_output=True, timeout=120)
                if done.returncode != 0:
                    return {'ok': False, 'error': 'bad_audio', 'message': 'That audio could not be played.'}
            started = time.monotonic()
            done = subprocess.run(['paplay', '-d', self.names['feed'], playable],  # noqa: S603, S607
                                  capture_output=True, timeout=600)
            if done.returncode != 0:
                return {'ok': False, 'error': 'play_failed',
                        'message': (done.stderr or b'').decode('utf-8', 'replace')[-300:] or 'Playing failed.'}
        return {'ok': True, 'seconds': round(time.monotonic() - started, 1)}


def _plain_wav(audio: bytes) -> bool:
    return audio[:4] == b'RIFF' and audio[8:12] == b'WAVE'


def rms(frame: bytes) -> float:
    samples = array('h')
    samples.frombytes(frame[: len(frame) - len(frame) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def wav_bytes(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


class Segmenter:
    """Turns 100 ms frames into pieces of speech. Separate from the recorder
    so it can be tested with made-up sound."""

    def __init__(self, threshold: float = SPEECH_RMS):
        self.threshold = threshold
        self._frames: list[bytes] = []
        self._sound_frames = 0
        self._quiet_run = 0
        self._lead: deque[bytes] = deque(maxlen=3)  # keep the start of the first word

    def push(self, frame: bytes) -> bytes | None:
        """Add a frame; returns a finished piece of PCM, or None."""
        loud = rms(frame) >= self.threshold
        if not self._frames:
            if not loud:
                self._lead.append(frame)
                return None
            self._frames = [*self._lead, frame]
            self._lead.clear()
            self._sound_frames = 1
            self._quiet_run = 0
            return None
        self._frames.append(frame)
        if loud:
            self._sound_frames += 1
            self._quiet_run = 0
        else:
            self._quiet_run += 1
        length = len(self._frames) * FRAME_S
        if self._quiet_run * FRAME_S >= PAUSE_S or length >= MAX_PIECE_S:
            return self.flush()
        return None

    def flush(self) -> bytes | None:
        frames, sound = self._frames, self._sound_frames
        self._frames, self._sound_frames, self._quiet_run = [], 0, 0
        if sound * FRAME_S < MIN_SOUND_S:
            return None
        return b''.join(frames)

    @staticmethod
    def is_loud(frame: bytes, threshold: float = SPEECH_RMS) -> bool:
        return rms(frame) >= threshold


class Recorder:
    """Listens to one PulseAudio source with ``parec`` and keeps the pieces of
    speech it hears until the engine reads them."""

    def __init__(self, source: str):
        self.source = source
        self.seq = 0
        self.last_sound = 0.0  # time.time() of the last loud frame
        self.error = ''
        self._pieces: deque[dict] = deque(maxlen=KEEP_PIECES)
        self._guard = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._proc = subprocess.Popen(  # noqa: S603
            ['parec', '-d', self.source, '--format=s16le', f'--rate={RATE}', '--channels=1', '--raw',  # noqa: S607
             '--latency-msec=100'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        self._thread = threading.Thread(target=self._loop, name=f'rec-{self.source}', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._thread is not None:
            self._thread.join(timeout=2)  # _loop flushes speech that has not reached a pause yet

    def _loop(self) -> None:
        seg = Segmenter()
        stream = self._proc.stdout if self._proc else None
        try:
            while stream is not None and not self._stopping:
                frame = stream.read(FRAME_BYTES)
                if not frame:
                    break
                if Segmenter.is_loud(frame, seg.threshold):
                    self.last_sound = time.time()
                piece = seg.push(frame)
                if piece:
                    self._add(piece)
            last = seg.flush()
            if last:
                self._add(last)
        except Exception as exc:  # keep what was heard; say why it stopped
            logger.exception('recorder %s stopped', self.source)
            self.error = str(exc)[:200]
        if not self._stopping and not self.error:
            self.error = 'The recorder stopped.'

    def _add(self, pcm: bytes) -> None:
        with self._guard:
            self.seq += 1
            self._pieces.append({'seq': self.seq, 'at': time.time(), 'seconds': round(len(pcm) / (RATE * 2), 2),
                                 'audio': base64.b64encode(wav_bytes(pcm)).decode()})

    def pieces_after(self, after: int, ack: int | None = None) -> list[dict]:
        """Pieces newer than ``after``. Pieces up to ``ack`` (the engine has
        their text) are dropped; without ``ack``, asking for newer ones hands
        over the older ones. Pieces between the two are kept, so an engine
        that restarts before transcribing them can read them again."""
        keep_from = after if ack is None else min(ack, after)
        with self._guard:
            while self._pieces and self._pieces[0]['seq'] <= keep_from:
                self._pieces.popleft()
            return [p for p in self._pieces if p['seq'] > after]
