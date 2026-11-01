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
    assert json.loads(row.api_keys) == {'openai': 'sk-user-abcd1234'}

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
