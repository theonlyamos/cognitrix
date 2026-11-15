"""Text an agent by SMS through a Twilio number."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cognitrix.channels import sms
from cognitrix.channels.base import BotInfo, Button, ChannelAuthError, Handlers

SID = 'AC' + 'a' * 32
PHONE = '+15550102000'


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'sms.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield


def test_signature_matches_twilios_documented_example():
    params = {'CallSid': 'CA1234567890ABCDE', 'Caller': '+12349013030', 'Digits': '1234',
              'From': '+12349013030', 'To': '+18005551212'}
    assert sms.signature('12345', 'https://mycompany.com/myapp.php?foo=1&bar=2', params) == \
        '0/KCTR6DLpKmkAf8muzZqo1nDgQ='


def test_phone_and_text_helpers():
    assert sms.normal_phone('+1 (555) 010-2000') == PHONE
    assert sms.valid_phone(PHONE) and not sms.valid_phone('5550102000') and not sms.valid_phone('+0123')
    assert sms.valid_sid(SID) and not sms.valid_sid('AC123')
    assert sms.to_sms('## Done\n**Booked** [link](https://x.io)\n* one\n`code`') == \
        'Done\nBooked link (https://x.io)\n- one\ncode'


class FakeTwilio(sms.SmsTransport):
    def __init__(self):
        super().__init__('tok', SID, PHONE)
        self.calls = []

    async def _call(self, method, path, form=None):
        self.calls.append((method, path, form))
        if path.startswith('/IncomingPhoneNumbers'):
            return {'incoming_phone_numbers': [{'phone_number': PHONE}]}
        return {'sid': f'SM{len(self.calls)}'}


def _params(body, sender='+15559990000', sid='SM1'):
    return {'AccountSid': SID, 'From': sender, 'To': PHONE, 'Body': body, 'MessageSid': sid}


async def _receive(transport, params, url='https://cog.example.com/api/v1/channels/sms/x/webhook'):
    await transport.receive([url], sms.signature('tok', url, params), params)


async def test_receive_checks_the_signature_and_maps_numbered_replies_to_buttons():
    transport = FakeTwilio()
    got, presses = [], []

    async def on_message(msg):
        got.append(msg)

    async def on_button(press):
        presses.append(press)

    transport._handlers = Handlers(on_message=on_message, on_button=on_button)
    url = 'https://cog.example.com/api/v1/channels/sms/x/webhook'
    with pytest.raises(sms.SmsAuthError):
        await transport.receive([url], 'forged', _params('hi'))
    with pytest.raises(sms.SmsAuthError):
        await transport.receive([url], '', _params('hi'))

    await _receive(transport, _params('hello agent'))
    await _receive(transport, _params('hello agent'))  # Twilio retried: read once
    assert [m.text for m in got] == ['hello agent'] and got[0].chat_id == '+15559990000'

    # Another account's or number's texts are not ours.
    await _receive(transport, {**_params('x', sid='SM2'), 'To': '+15550000000'})
    assert len(got) == 1

    ref = await transport.send('+15559990000', '**Run it?**', [Button('Approve', 'ca:r1:1'), Button('Deny', 'ca:r1:0')])
    body = transport.calls[-1][2]['Body']
    assert body.startswith('Run it?') and '1. Approve\n2. Deny' in body
    await _receive(transport, _params('2', sid='SM3'))
    assert presses[0].data == 'ca:r1:0' and presses[0].message_ref == ref
    # The choice was used: a later "1" is an ordinary message.
    await _receive(transport, _params('1', sid='SM4'))
    assert got[-1].text == '1'


async def test_check_wants_a_number_from_the_account():
    transport = FakeTwilio()
    assert await transport.check() == BotInfo(name=PHONE, link_url=f'sms:{PHONE}')
    with pytest.raises(ChannelAuthError):
        await sms.SmsTransport('tok', 'AC1', PHONE).check()


def _ctx(user_id):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=SimpleNamespace(id=user_id, email=f'{user_id}@x.com'))


async def test_settings_save_twilio_details_and_show_the_webhook(db, monkeypatch):
    from cognitrix.api.routes import channels
    from cognitrix.channels import service as svc

    made = []

    def factory(row):
        made.append(row)
        return FakeTwilio()

    monkeypatch.setattr(svc, 'make_transport', factory)
    with pytest.raises(HTTPException) as err:
        await channels.update_channel('sms', channels.ChannelUpdate(bot_token='tok', app_id='AC1', phone_number=PHONE),
                                      _ctx('u1'))
    assert 'Account SID' in err.value.detail
    data = await channels.update_channel('sms', channels.ChannelUpdate(
        bot_token='secret-tok', app_id=SID, phone_number='+1 555 010 2000'), _ctx('u1'))
    assert made[-1].app_token == f'{SID}|{PHONE}'
    assert data['bot_name'] == PHONE and data['needs_public_url']
    assert data['webhook_path'].endswith('/webhook') and '/channels/sms/' in data['webhook_path']
    assert data['link_url'] == f'sms:{PHONE}?body={data["link_code"]}'
    assert 'secret-tok' not in str(data)
    with pytest.raises(HTTPException) as err:
        await channels.update_channel('sms', channels.ChannelUpdate(
            bot_token='other', app_id=SID, phone_number=PHONE), _ctx('u2'))
    assert err.value.status_code == 409


async def test_strangers_get_no_paid_replies(db):
    from cognitrix.channels.base import Incoming
    from cognitrix.channels.service import ChannelService
    from cognitrix.models.chat_channel import ChatChannel

    row = ChatChannel(user_id='u1', kind='sms', bot_token='tok', app_token=f'{SID}|{PHONE}', link_code='ABCDEFGHJK')
    await row.save()
    row = await ChatChannel.get(row.id)
    transport = FakeTwilio()
    service = ChannelService(transport_factory=lambda _r: transport)
    service._transports[row.id] = transport
    msg = Incoming(chat_id='+15559990000', sender_id='+15559990000', sender_name='', text='wrong code')
    await service._on_message(row.id, msg)
    assert transport.calls == []
    await ChatChannel.update_one({'id': row.id}, {'chat_id': '+15551112222', 'peer_id': '+15551112222'})
    await service._on_message(row.id, msg)
    assert transport.calls == [] and not service._turns
    await asyncio.sleep(0)


def test_twilio_urls_cover_proxies():
    from cognitrix.api.routes.channels import _twilio_urls

    class Url:
        path, query, netloc = '/api/v1/channels/sms/x/webhook', '', '10.0.0.5:8000'

        def __str__(self):
            return 'http://10.0.0.5:8000/api/v1/channels/sms/x/webhook'

    request = SimpleNamespace(url=Url(), headers={'x-forwarded-host': 'cog.example.com', 'x-forwarded-proto': 'https'})
    urls = _twilio_urls(request, 'https://public.example.com/')
    assert 'https://cog.example.com/api/v1/channels/sms/x/webhook' in urls
    assert 'https://public.example.com/api/v1/channels/sms/x/webhook' in urls
