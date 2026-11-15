"""Voice input: per-user speech-to-text settings and transcription."""
import json
import struct
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'voice.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()

    for name in ('OPENAI', 'GOOGLE', 'GROQ', 'DEEPGRAM'):
        monkeypatch.delenv(f'{name}_API_KEY', raising=False)
        monkeypatch.delenv(f'{name}_BASE_URL', raising=False)
    from cognitrix.config import settings
    for attr in ('openai_api_key', 'google_api_key', 'groq_api_key', 'deepgram_api_key'):
        monkeypatch.setattr(settings, attr, '', raising=False)
    from cognitrix.api.routes import audio
    audio._recent.clear()
    yield


async def _user(name='Ada', email='ada@example.com'):
    from cognitrix.models import User

    user = User(name=name, email=email, password='x' * 20)
    await user.save()
    return await User.find_one({'email': email})


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


def _wav(seconds: float, rate: int = 16000) -> bytes:
    pcm = b'\x00\x00' * int(rate * seconds)
    header = b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVE'
    header += b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, rate, rate * 2, 2, 16)
    header += b'data' + struct.pack('<I', len(pcm))
    return header + pcm


class _Request:
    def __init__(self, body: bytes, content_type: str = 'audio/wav'):
        self._body = body
        self.headers = {'content-type': content_type, 'content-length': str(len(body))}

    async def stream(self):
        for i in range(0, len(self._body), 65536):
            yield self._body[i:i + 65536]


async def test_auto_picks_first_provider_with_a_server_key_and_hides_keys(db, monkeypatch):
    from cognitrix.api.routes import audio

    monkeypatch.setenv('GROQ_API_KEY', 'gsk-server-key-123456')
    data = await audio.get_voice_settings(_ctx(await _user()))

    assert data['provider'] == ''
    assert data['effective'] == {'provider': 'groq', 'model': 'whisper-large-v3-turbo', 'ready': True}
    groq = next(p for p in data['providers'] if p['id'] == 'groq')
    assert groq['server_key'] is True and groq['user_key'] is False
    assert 'gsk-server-key' not in repr(data)


async def test_user_key_and_model_are_saved_per_user_and_never_returned(db):
    from cognitrix.api.routes import audio
    from cognitrix.models.voice_settings import VoiceSettings

    ada, bob = await _user(), await _user('Bob', 'bob@example.com')
    data = await audio.update_voice_settings(
        audio.VoiceSettingsUpdate(provider='openai', model='gpt-4o-mini-transcribe', api_key=' sk-user-abcd1234 '),
        _ctx(ada),
    )
    assert data['provider'] == 'openai'
    assert data['effective'] == {'provider': 'openai', 'model': 'gpt-4o-mini-transcribe', 'ready': True}
    openai = next(p for p in data['providers'] if p['id'] == 'openai')
    assert openai['user_key'] is True and openai['key_hint'] == '…1234'
    assert 'sk-user-abcd1234' not in repr(data)

    row = await VoiceSettings.find_one({'user_id': ada.id})
    # Saved encrypted; only the server can read it back.
    from cognitrix.common.secret_box import unseal
    assert row.api_keys.startswith('enc:v1:') and 'sk-user' not in row.api_keys
    assert json.loads(unseal(row.api_keys)) == {'openai': 'sk-user-abcd1234'}

    # Bob sees none of it.
    other = await audio.get_voice_settings(_ctx(bob))
    assert other['effective']['ready'] is False
    assert not any(p['user_key'] for p in other['providers'])

    # Switching provider resets the model; clearing the key removes it.
    data = await audio.update_voice_settings(audio.VoiceSettingsUpdate(provider='groq'), _ctx(ada))
    assert data['model'] == '' and data['effective']['model'] == 'whisper-large-v3-turbo'
    await audio.update_voice_settings(audio.VoiceSettingsUpdate(provider='openai', api_key=''), _ctx(ada))
    row = await VoiceSettings.find_one({'user_id': ada.id})
    assert row.api_keys == ''


async def test_settings_reject_bad_input(db):
    from cognitrix.api.routes import audio

    ctx = _ctx(await _user())
    for body in (
        audio.VoiceSettingsUpdate(provider='anthropic'),
        audio.VoiceSettingsUpdate(api_key='sk-x'),
        audio.VoiceSettingsUpdate(provider='openai', model='two words'),
        audio.VoiceSettingsUpdate(provider='openai', api_key='has space'),
    ):
        with pytest.raises(HTTPException) as exc:
            await audio.update_voice_settings(body, ctx)
        assert exc.value.status_code == 400


async def test_transcription_without_any_key_explains_what_to_do(db):
    from cognitrix.api.routes import audio

    with pytest.raises(HTTPException) as exc:
        await audio.create_transcription(_Request(_wav(1)), None, _ctx(await _user()))
    assert exc.value.status_code == 400
    assert 'Settings' in exc.value.detail


async def test_transcription_uses_the_users_provider_model_and_key(db, monkeypatch):
    from cognitrix.api.routes import audio

    calls = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text='  hello   world ')

    fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=fake_create)))
    seen = {}

    def fake_get_client(base_url, api_key, headers=None):
        seen.update(base_url=base_url, api_key=api_key)
        return fake_client

    monkeypatch.setattr('cognitrix.providers.transcription._get_or_create_client', fake_get_client)
    ada = await _user()
    await audio.update_voice_settings(
        audio.VoiceSettingsUpdate(provider='groq', model='whisper-large-v3', api_key='gsk-user-999999'), _ctx(ada)
    )
    result = await audio.create_transcription(_Request(_wav(2)), 'en', _ctx(ada))

    assert result == {'text': 'hello world', 'provider': 'groq', 'model': 'whisper-large-v3'}
    assert seen == {'base_url': 'https://api.groq.com/openai/v1', 'api_key': 'gsk-user-999999'}
    assert calls[0]['model'] == 'whisper-large-v3'
    assert calls[0]['language'] == 'en'
    assert calls[0]['file'][0] == 'recording.wav'


async def test_gemini_gets_long_clips_in_pieces(db, monkeypatch):
    from cognitrix.providers import transcription

    sent = []

    async def fake_chat(**kwargs):
        sent.append(kwargs)
        n = len(sent)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=f'part {n}'))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_chat)))
    monkeypatch.setattr(transcription, '_get_or_create_client', lambda *a, **k: client)

    text = await transcription.transcribe(
        _wav(9 * 60, rate=8000), mime='audio/wav', provider='google', model='gemini-3.5-flash', api_key='g-key'
    )
    assert text == 'part 1 part 2 part 3'
    audio_part = sent[0]['messages'][0]['content'][1]
    assert audio_part['type'] == 'input_audio' and audio_part['input_audio']['format'] == 'wav'


async def test_audio_limits(db):
    from cognitrix.providers import transcription

    with pytest.raises(transcription.TranscriptionError) as exc:
        transcription.check_audio(b'')
    assert exc.value.status_code == 400
    with pytest.raises(transcription.TranscriptionError) as exc:
        transcription.check_audio(_wav(11 * 60, rate=8000))
    assert exc.value.status_code == 413
    info = transcription.parse_wav(_wav(3))
    assert info.sample_rate == 16000 and round(info.seconds) == 3


async def test_provider_errors_become_friendly_messages(db, monkeypatch):
    from cognitrix.providers import transcription

    class Rejected(Exception):
        status_code = 401

    async def boom(**kwargs):
        raise Rejected('bad key')

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=boom)))
    monkeypatch.setattr(transcription, '_get_or_create_client', lambda *a, **k: client)
    with pytest.raises(transcription.TranscriptionError) as exc:
        await transcription.transcribe(_wav(1), mime='audio/wav', provider='openai', model='', api_key='sk-x')
    assert exc.value.status_code == 400
    assert 'rejected the API key' in str(exc.value)


async def test_rate_limit(db, monkeypatch):
    from cognitrix.api.routes import audio

    monkeypatch.setattr(audio, 'RATE_LIMIT_PER_MINUTE', 2)
    audio._check_rate('u1')
    audio._check_rate('u1')
    with pytest.raises(HTTPException) as exc:
        audio._check_rate('u1')
    assert exc.value.status_code == 429
    audio._check_rate('u2')


# ------------------------------------------------------------- spoken replies

async def test_speech_options_follow_the_keys(db, monkeypatch):
    from cognitrix.api.routes import audio

    user = await _user()
    options = await audio.speech_options(_ctx(user))
    assert [p['ready'] for p in options['providers']] == [False, False]

    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    options = await audio.speech_options(_ctx(user))
    assert {p['id']: p['ready'] for p in options['providers']} == {'openai': True, 'google': False}
    assert options['providers'][0]['voices'][0] == 'marin'


async def test_speech_needs_a_key_and_returns_audio(db, monkeypatch):
    from cognitrix.api.routes import audio

    user = await _user()
    with pytest.raises(HTTPException) as refused:
        await audio.create_speech(audio.SpeechRequest(text='Hello'), _ctx(user))
    assert refused.value.status_code == 400

    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    seen = {}

    async def fake_speak(text, *, provider, voice, api_key):
        seen.update(text=text, provider=provider, voice=voice, api_key=api_key)
        return b'ID3audio', 'audio/mpeg'

    monkeypatch.setattr(audio, 'speak', fake_speak)
    audio._recent_speech.clear()
    response = await audio.create_speech(audio.SpeechRequest(text='Hello', voice='nova'), _ctx(user))
    assert response.body == b'ID3audio' and response.media_type == 'audio/mpeg'
    assert seen == {'text': 'Hello', 'provider': 'openai', 'voice': 'nova', 'api_key': 'sk-test'}


def test_gemini_pcm_is_wrapped_as_wav():
    from cognitrix.providers.speech import _rate_from_mime, pcm_to_wav
    from cognitrix.providers.transcription import parse_wav

    wav = pcm_to_wav(b'\x00\x00' * 24000, _rate_from_mime('audio/L16;codec=pcm;rate=24000'))
    info = parse_wav(wav)
    assert info.sample_rate == 24000 and round(info.seconds) == 1


# ------------------------------------------------- OpenAI speech via Realtime

def test_openai_speech_defaults_to_the_realtime_model(monkeypatch):
    from cognitrix.providers import speech

    monkeypatch.delenv('COGNITRIX_OPENAI_SPEECH_MODEL', raising=False)
    model = speech.SPEECH_PROVIDERS['openai'].model
    assert model == 'gpt-realtime-2.1-mini'
    assert not speech.uses_speech_endpoint(model)
    assert speech.uses_speech_endpoint('gpt-4o-mini-tts-2025-12-15')
    assert speech.uses_speech_endpoint('tts-1-hd')
    assert speech.realtime_url('https://api.openai.com/v1/', model) == \
        'wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1-mini'


def test_realtime_events_read_the_text_out_of_band():
    from cognitrix.providers.speech import READ_ALOUD_INSTRUCTIONS, realtime_events

    session, response = realtime_events('Say hi', 'cedar')
    assert session['type'] == 'session.update'
    assert session['session']['output_modalities'] == ['audio']
    assert session['session']['audio']['output'] == {'format': {'type': 'audio/pcm', 'rate': 24000}, 'voice': 'cedar'}
    body = response['response']
    assert response['type'] == 'response.create'
    assert body['conversation'] == 'none'
    assert body['instructions'] == READ_ALOUD_INSTRUCTIONS
    assert body['input'][0]['content'] == [{'type': 'input_text', 'text': 'Say hi'}]


class _FakeWS:
    def __init__(self, events):
        import json
        self._events = [json.dumps(e) for e in events]
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


async def test_realtime_audio_is_collected_until_done():
    import base64

    from cognitrix.providers.speech import collect_realtime_audio

    ws = _FakeWS([
        {'type': 'session.updated'},
        {'type': 'response.output_audio.delta', 'delta': base64.b64encode(b'\x01\x02').decode()},
        {'type': 'response.output_audio_transcript.delta', 'delta': 'hi'},
        {'type': 'response.output_audio.delta', 'delta': base64.b64encode(b'\x03\x04').decode()},
        {'type': 'response.done', 'response': {'status': 'completed'}},
        {'type': 'response.output_audio.delta', 'delta': base64.b64encode(b'late').decode()},
    ])
    assert await collect_realtime_audio(ws) == b'\x01\x02\x03\x04'


async def test_realtime_errors_become_friendly_messages(monkeypatch):
    import sys
    import types

    from cognitrix.providers import speech

    events = [{'type': 'error', 'error': {'type': 'invalid_request_error', 'code': 'invalid_api_key'}}]

    class _Conn:
        def __init__(self, url, **kwargs):
            self.url = url
            self.kwargs = kwargs

        async def __aenter__(self):
            return _FakeWS(events)

        async def __aexit__(self, *exc):
            return False

    fake = types.SimpleNamespace(connect=_Conn, exceptions=types.SimpleNamespace(InvalidStatus=type('InvalidStatus', (Exception,), {})))
    monkeypatch.setitem(sys.modules, 'websockets', fake)
    monkeypatch.delenv('COGNITRIX_OPENAI_SPEECH_MODEL', raising=False)
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    with pytest.raises(speech.SpeechError, match='rejected the API key'):
        await speech.speak('Hello', provider='openai', voice='marin', api_key='sk-test')


async def test_realtime_speech_returns_wav(monkeypatch):
    import base64
    import sys
    import types

    from cognitrix.providers import speech
    from cognitrix.providers.transcription import parse_wav

    seen = {}
    pcm = b'\x00\x00' * 2400
    events = [
        {'type': 'response.output_audio.delta', 'delta': base64.b64encode(pcm).decode()},
        {'type': 'response.done', 'response': {'status': 'completed'}},
    ]

    class _Conn:
        def __init__(self, url, **kwargs):
            seen['url'] = url
            seen['headers'] = kwargs.get('additional_headers')
            self.ws = _FakeWS(events)

        async def __aenter__(self):
            return self.ws

        async def __aexit__(self, *exc):
            seen['sent'] = self.ws.sent
            return False

    fake = types.SimpleNamespace(connect=_Conn, exceptions=types.SimpleNamespace(InvalidStatus=type('InvalidStatus', (Exception,), {})))
    monkeypatch.setitem(sys.modules, 'websockets', fake)
    monkeypatch.delenv('COGNITRIX_OPENAI_SPEECH_MODEL', raising=False)
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    # A voice only the old endpoint had falls back to the first Realtime voice.
    audio, mime = await speech.speak('Hello', provider='openai', voice='nova', api_key='sk-test')
    assert mime == 'audio/wav'
    assert parse_wav(audio).sample_rate == 24000
    assert seen['url'].endswith('/realtime?model=gpt-realtime-2.1-mini')
    assert seen['headers'] == {'Authorization': 'Bearer sk-test'}
    assert '"voice": "marin"' in seen['sent'][0]
