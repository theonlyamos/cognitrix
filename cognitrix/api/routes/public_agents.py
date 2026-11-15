"""Public agent links: the owner's settings, the visitor API and the chat page.

* ``/api/v1/public-links/agents/{agent_id}``: the agent owner turns the link
  on or off and sets it up, reads visitors' chats (browser sessions only).
* ``/api/v1/public/agents/{token}``: what the chat page calls. No sign-in; a
  visitor proves a chat is theirs with the visitor token the page keeps.
* ``/chat/{token}``: the chat page itself, a small page with its own strict
  CSP that websites may put in a frame, and ``/chat/{token}/widget.js``, the
  script a website adds to show the chat as a bubble in its corner.
"""
from __future__ import annotations

import html
import json
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.public_agents import service
from cognitrix.public_agents.models import (
    GREETING_MAX,
    INSTRUCTIONS_MAX,
    MAX_DAILY_LIMIT,
    MAX_VISITOR_HOURLY_LIMIT,
    TITLE_MAX,
    PublicAgentLink,
)

public_links_api = APIRouter(prefix='/public-links', dependencies=[Depends(jwt_only)])
public_agent_api = APIRouter(prefix='/public/agents')
public_chat_pages = APIRouter()


# ------------------------------------------------------------------ the owner

class LinkUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool | None = None
    title: str | None = Field(default=None, max_length=TITLE_MAX)
    greeting: str | None = Field(default=None, max_length=GREETING_MAX)
    instructions: str | None = Field(default=None, max_length=INSTRUCTIONS_MAX)
    web_search: bool | None = None
    sites: list[str] | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=MAX_DAILY_LIMIT)
    visitor_hourly_limit: int | None = Field(default=None, ge=1, le=MAX_VISITOR_HOURLY_LIMIT)


async def _owned_agent(agent_id: str, ctx: AuthContext):
    from cognitrix.agents import Agent
    from cognitrix.agents import access as agent_access

    agent = await Agent.find_one({'id': str(agent_id)})
    if agent is None or not agent_access.can_view(agent, agent_access.owner_key(ctx.user)):
        raise HTTPException(status_code=404, detail='Agent not found')
    if not agent.owner_id or not await agent_access.can_manage(agent, ctx.user):
        raise HTTPException(status_code=403, detail='Only the agent’s owner can give it a public link')
    return agent


def _view(link: PublicAgentLink | None, agent: Any) -> dict[str, Any]:
    allowed = service.public_links_allowed()
    tools = {str(getattr(t, 'name', '')).lower() for t in getattr(agent, 'tools', None) or []}
    base = {
        'allowed': allowed,
        'agent_name': agent.name,
        'has_knowledge': bool(getattr(agent, 'knowledge', None)),
        'has_web_search': bool(tools & {'search', 'tavily search', 'brave search'}),
    }
    if link is None:
        return {**base, 'link': None}
    today = service.utc_day()
    return {**base, 'link': {
        'enabled': bool(link.enabled),
        'url': f'/chat/{link.token}',
        'widget_url': f'/chat/{link.token}/widget.js',
        'title': link.title,
        'greeting': link.greeting,
        'instructions': link.instructions,
        'web_search': bool(link.web_search),
        'sites': list(link.sites or []),
        'daily_limit': link.daily_limit or service.DEFAULT_DAILY_LIMIT,
        'visitor_hourly_limit': link.visitor_hourly_limit or service.DEFAULT_VISITOR_HOURLY_LIMIT,
        'used_today': link.day_count if link.day == today else 0,
        'created': link.created,
        'updated': link.updated,
    }}


@public_links_api.get('/agents/{agent_id}')
async def get_link(agent_id: str, ctx: AuthContext = Depends(get_auth_context)):
    agent = await _owned_agent(agent_id, ctx)
    return _view(await service.link_for_agent(agent.id), agent)


@public_links_api.put('/agents/{agent_id}')
async def set_link(agent_id: str, body: LinkUpdate, ctx: AuthContext = Depends(get_auth_context)):
    """Turn the link on (making it the first time) or change its settings."""
    from cognitrix.agents import access as agent_access

    agent = await _owned_agent(agent_id, ctx)
    link = await service.link_for_agent(agent.id)
    if link is None and body.enabled is not False and not service.public_links_allowed():
        raise HTTPException(status_code=403, detail='Public agent links are turned off on this server')
    fields: dict[str, Any] = {}
    for name in ('enabled', 'web_search', 'daily_limit', 'visitor_hourly_limit'):
        value = getattr(body, name)
        if value is not None:
            fields[name] = value
    for name in ('title', 'greeting', 'instructions'):
        value = getattr(body, name)
        if value is not None:
            fields[name] = value.strip()
    if body.sites is not None:
        try:
            fields['sites'] = service.normalize_sites(body.sites)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail='List up to 20 websites as addresses like https://example.com',
            ) from None
    now = service.now_str()
    if link is None:
        link = PublicAgentLink(
            agent_id=str(agent.id), created_by=agent_access.owner_key(ctx.user), token=service.new_token(),
            created=now, updated=now, **{'enabled': True, **fields},
        )
        await link.save()
    elif fields:
        fields['updated'] = now
        await PublicAgentLink.update_one({'id': link.id}, fields)
        link = await PublicAgentLink.get(link.id)
    return _view(link, agent)


@public_links_api.post('/agents/{agent_id}/rotate')
async def rotate_link(agent_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """A new link address. The old one (and websites using it) stop working."""
    agent = await _owned_agent(agent_id, ctx)
    link = await service.link_for_agent(agent.id)
    if link is None:
        raise HTTPException(status_code=404, detail='This agent has no public link')
    await PublicAgentLink.update_one({'id': link.id}, {'token': service.new_token(), 'updated': service.now_str()})
    return _view(await PublicAgentLink.get(link.id), agent)


@public_links_api.delete('/agents/{agent_id}')
async def delete_link(agent_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """Remove the link and every visitor's chat on it."""
    agent = await _owned_agent(agent_id, ctx)
    link = await service.link_for_agent(agent.id)
    if link is not None:
        await service.delete_link_chats(str(link.id))
        await PublicAgentLink.delete_many({'id': link.id})
    return _view(None, agent)


async def _link_chats(agent_id: str, ctx: AuthContext):
    agent = await _owned_agent(agent_id, ctx)
    link = await service.link_for_agent(agent.id)
    if link is None:
        raise HTTPException(status_code=404, detail='This agent has no public link')
    return agent, link


def _when(session: Any, name: str) -> str:
    value = getattr(session, name, None)
    return value.isoformat() if hasattr(value, 'isoformat') else str(value or '')


@public_links_api.get('/agents/{agent_id}/chats')
async def list_link_chats(agent_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """Visitors' chats on the link, newest first (no visitor details: there are none)."""
    from cognitrix.sessions.base import Session

    _, link = await _link_chats(agent_id, ctx)
    rows = await Session.find({'public_link_id': str(link.id)}) or []
    chats = []
    for row in rows:
        messages = service.visitor_messages(row)
        if not messages:
            continue
        first = next((m['content'] for m in messages if m['role'] == 'user'), '')
        chats.append({
            'id': str(row.id),
            'preview': ' '.join(first.split())[:140],
            'messages': len(messages),
            'started': _when(row, 'created_at') or str(row.datetime or ''),
            'updated': _when(row, 'updated_at'),
            'running': service.is_running(str(row.id)),
        })
    chats.sort(key=lambda c: c['updated'], reverse=True)
    return {'chats': chats[:200], 'total': len(chats)}


async def _one_link_chat(agent_id: str, chat_id: str, ctx: AuthContext):
    from cognitrix.sessions.base import Session

    _, link = await _link_chats(agent_id, ctx)
    session = await Session.get(str(chat_id))
    if session is None or str(getattr(session, 'public_link_id', '') or '') != str(link.id):
        raise HTTPException(status_code=404, detail='Chat not found')
    return session


@public_links_api.get('/agents/{agent_id}/chats/{chat_id}')
async def read_link_chat(agent_id: str, chat_id: str, ctx: AuthContext = Depends(get_auth_context)):
    session = await _one_link_chat(agent_id, chat_id, ctx)
    return {'id': str(session.id), 'messages': service.visitor_messages(session),
            'running': service.is_running(str(session.id))}


@public_links_api.delete('/agents/{agent_id}/chats/{chat_id}')
async def delete_link_chat(agent_id: str, chat_id: str, ctx: AuthContext = Depends(get_auth_context)):
    from cognitrix.sessions.base import Session

    session = await _one_link_chat(agent_id, chat_id, ctx)
    await Session.delete_many({'id': str(session.id)})
    return {'deleted': True}


# --------------------------------------------------------------- the visitor

def _gone() -> HTTPException:
    return HTTPException(status_code=404, detail='This chat link was turned off or never existed.')


async def _live(token: str):
    found = await service.live_link(token)
    if found is None:
        raise _gone()
    return found


def _ip(request: Request) -> str:
    client = getattr(request, 'client', None)
    return str(getattr(client, 'host', '') or '?')


def _visitor(value: str | None) -> str:
    token = str(value or '').strip()
    if not token or len(token) > 100:
        raise HTTPException(status_code=401, detail='Start a new chat.')
    return service.visitor_key(token)


def _public_info(link: PublicAgentLink, agent: Any) -> dict[str, Any]:
    avatar = getattr(agent, 'avatar', None)
    avatar = avatar if isinstance(avatar, dict) else {}
    return {
        'title': link.title or agent.name,
        'agent': agent.name,
        'greeting': link.greeting,
        'emoji': str(avatar.get('emoji') or '')[:16],
        'hue': avatar.get('hue') if isinstance(avatar.get('hue'), int) else None,
    }


@public_agent_api.get('/{token}')
async def public_info(token: str):
    link, agent, _ = await _live(token)
    return _public_info(link, agent)


@public_agent_api.post('/{token}/chats')
async def public_new_chat(token: str, request: Request):
    """A new chat. The visitor token in the reply is the only way back into it."""
    link, agent, owner = await _live(token)
    try:
        service.check_new_chat(_ip(request))
    except service.LimitError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from None
    visitor_token = service.new_visitor_token()
    session = await service.new_chat(link, agent, owner, service.visitor_key(visitor_token))
    return {'chat_id': str(session.id), 'visitor_token': visitor_token}


@public_agent_api.get('/{token}/chats/{chat_id}')
async def public_read_chat(token: str, chat_id: str, x_visitor_token: str | None = Header(default=None)):
    link, _, _ = await _live(token)
    session = await service.visitor_chat(link, chat_id, _visitor(x_visitor_token))
    if session is None:
        raise HTTPException(status_code=404, detail='Start a new chat.')
    return {'messages': service.visitor_messages(session), 'running': service.is_running(str(session.id))}


class VisitorMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')

    message: str = Field(min_length=1, max_length=service.MESSAGE_MAX_CHARS)


@public_agent_api.post('/{token}/chats/{chat_id}/messages')
async def public_send(token: str, chat_id: str, body: VisitorMessage, request: Request,
                      x_visitor_token: str | None = Header(default=None)):
    """Send a message; the reply streams back as server-sent events:
    ``text`` (a piece of the reply), ``tool`` (looking something up),
    then ``done`` (the whole reply) or ``error``."""
    link, agent, owner = await _live(token)
    session = await service.visitor_chat(link, chat_id, _visitor(x_visitor_token))
    if session is None:
        raise HTTPException(status_code=404, detail='Start a new chat.')
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail='Write a message first.')
    if service.is_running(str(session.id)):
        raise HTTPException(status_code=409, detail='Still answering your last message.')
    if service.user_message_count(session) >= service.MAX_VISITOR_MESSAGES_PER_CHAT:
        raise HTTPException(status_code=429, detail='This chat is full. Start a new one to keep going.')
    owner_display = await service.owner_name(owner)
    try:
        await service.take_message(link, session.visitor_key or '', _ip(request), str(session.id))
    except service.LimitError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from None
    # No await between reserving the slot and starting the turn.
    try:
        queue = service.start_turn(link, agent, owner, session, text, owner_display)
    except BaseException:
        service.release(link, str(session.id))
        raise

    async def events():
        async for item in service.follow(queue):
            yield {'event': 'message', 'data': json.dumps(item)}

    return EventSourceResponse(events(), ping=15)


# -------------------------------------------------------- the page and widget

def _page_csp(link: PublicAgentLink, nonce: str) -> str:
    return (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        f"style-src 'nonce-{nonce}'; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; "
        f"frame-ancestors {service.frame_ancestors(link)}"
    )


@public_chat_pages.get('/chat/{token}', include_in_schema=False)
async def chat_page(token: str):
    found = await service.live_link(token)
    if found is None:
        return HTMLResponse(_unavailable_page(), status_code=404, headers={
            'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors *",
            'Cache-Control': 'no-store',
        })
    link, agent, _ = found
    nonce = secrets.token_urlsafe(16)
    info = _public_info(link, agent)
    page = _CHAT_PAGE.replace('{{NONCE}}', nonce).replace(
        '{{TITLE}}', html.escape(info['title'])).replace(
        '{{CONFIG}}', json.dumps({'token': token, **info}).replace('<', '\\u003c'))
    return HTMLResponse(page, headers={
        'Content-Security-Policy': _page_csp(link, nonce),
        'Cache-Control': 'no-store',
        'Referrer-Policy': 'no-referrer',
    })


@public_chat_pages.get('/chat/{token}/widget.js', include_in_schema=False)
async def chat_widget(token: str, request: Request):
    found = await service.live_link(token)
    if found is None:
        body = '/* This Cognitrix chat link was turned off. */'
        return Response(body, media_type='application/javascript', status_code=404)
    link, agent, _ = found
    origin = f'{request.url.scheme}://{request.url.netloc}'
    config = json.dumps({
        'src': f'{origin}/chat/{token}?embed=1',
        'title': link.title or agent.name,
    }).replace('<', '\\u003c')
    return Response(_WIDGET_JS.replace('{{CONFIG}}', config), media_type='application/javascript',
                    headers={'Cache-Control': 'no-cache', 'Cross-Origin-Resource-Policy': 'cross-origin'})


def _unavailable_page() -> str:
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chat unavailable</title><style>body{margin:0;font:15px/1.5 system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;background:#fafaf9;color:#1c1917;padding:16px;text-align:center}
@media (prefers-color-scheme:dark){body{background:#1c1b1a;color:#e7e5e4}}p{opacity:.7}</style></head>
<body><div><h1 style="font-size:18px">This chat isn't available</h1><p>The link was turned off or never existed.</p></div></body></html>"""


_CHAT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex">
<title>{{TITLE}}</title>
<style nonce="{{NONCE}}">
:root{--bg:#fafaf9;--panel:#ffffff;--fg:#1c1917;--dim:#6b6560;--line:#e7e2dc;--accent:#c2410c;--accent-fg:#fff;--me:#f1ece6;--danger:#b91c1c}
@media (prefers-color-scheme:dark){:root{--bg:#171615;--panel:#1f1d1b;--fg:#ece8e3;--dim:#a39d96;--line:#33302c;--accent:#f97316;--accent-fg:#1c1917;--me:#2a2724;--danger:#f87171}}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
header h1{font-size:15px;font-weight:600;margin:0;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
header button{font:inherit;font-size:12.5px;color:var(--dim);background:none;border:1px solid var(--line);border-radius:6px;padding:4px 10px;cursor:pointer}
header button:hover{color:var(--fg)}
.dot{width:28px;height:28px;border-radius:50%;background:var(--accent);color:var(--accent-fg);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;flex:none}
main{flex:1;min-height:0;overflow-y:auto;padding:16px}
.list{max-width:720px;margin:0 auto;display:flex;flex-direction:column;gap:12px}
.msg{max-width:88%;padding:9px 13px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}
.msg.user{align-self:flex-end;background:var(--me);border-bottom-right-radius:4px}
.msg.assistant{align-self:flex-start;background:var(--panel);border:1px solid var(--line);border-bottom-left-radius:4px}
.msg.error{align-self:center;color:var(--danger);font-size:13.5px;background:none;padding:2px}
.msg a{color:var(--accent)}
.msg code{font-family:ui-monospace,monospace;font-size:13px;background:var(--me);padding:1px 4px;border-radius:4px}
.status{align-self:flex-start;color:var(--dim);font-size:13px}
form{display:flex;gap:8px;padding:12px 16px calc(12px + env(safe-area-inset-bottom));border-top:1px solid var(--line);background:var(--panel)}
.inner{max-width:720px;margin:0 auto;display:flex;gap:8px;width:100%}
textarea{flex:1;min-width:0;resize:none;font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:9px 12px;max-height:140px;outline:none}
textarea:focus{border-color:var(--accent)}
form button{font:inherit;font-weight:600;background:var(--accent);color:var(--accent-fg);border:0;border-radius:10px;padding:0 16px;cursor:pointer}
form button:disabled{opacity:.5;cursor:default}
footer{text-align:center;font-size:11.5px;color:var(--dim);padding:0 16px 8px;background:var(--panel)}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
</style>
</head>
<body>
<header><div class="dot" aria-hidden="true" id="dot"></div><h1 id="title">{{TITLE}}</h1><button type="button" id="new">New chat</button></header>
<main id="scroll" aria-live="polite"><div class="list" id="list"></div></main>
<form id="form"><div class="inner"><label class="sr" for="box">Message</label><textarea id="box" rows="1" maxlength="4000" placeholder="Write a message…"></textarea><button id="send" type="submit">Send</button></div></form>
<footer>Answers come from an AI assistant and can be wrong. Don’t share passwords or private details.</footer>
<script nonce="{{NONCE}}">
(function(){
var C={{CONFIG}};
var api='/api/v1/public/agents/'+encodeURIComponent(C.token);
var key='cognitrix-chat:'+C.token;
var list=document.getElementById('list'),box=document.getElementById('box'),send=document.getElementById('send'),scroller=document.getElementById('scroll');
var dot=document.getElementById('dot');dot.textContent=(C.emoji||(C.agent||'?').charAt(0).toUpperCase());if(typeof C.hue==='number'){dot.style.background='hsl('+C.hue+' 55% 45%)';dot.style.color='#fff'}
var state=load(),busy=false;
function load(){try{return JSON.parse(localStorage.getItem(key)||'null')}catch(e){return null}}
function save(v){state=v;try{v?localStorage.setItem(key,JSON.stringify(v)):localStorage.removeItem(key)}catch(e){}}
function esc(s){return s.replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function fmt(t){var h=esc(t);h=h.replace(/`([^`\\n]+)`/g,'<code>$1</code>');h=h.replace(/\\*\\*([^*\\n]+)\\*\\*/g,'<strong>$1</strong>');
h=h.replace(/\\[([^\\]\\n]+)\\]\\((https?:\\/\\/[^\\s)]+)\\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
h=h.replace(/(^|[\\s(])(https?:\\/\\/[^\\s<)]+)/g,'$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');return h}
function add(role,text){var d=document.createElement('div');d.className='msg '+role;if(role==='assistant'){d.innerHTML=fmt(text)}else{d.textContent=text}list.appendChild(d);down();return d}
function status(text){var d=document.createElement('div');d.className='status';d.textContent=text;list.appendChild(d);down();return d}
function down(){scroller.scrollTop=scroller.scrollHeight}
function greet(){list.innerHTML='';if(C.greeting){add('assistant',C.greeting)}}
function setBusy(v){busy=v;send.disabled=v;}
async function req(path,opts){opts=opts||{};opts.headers=Object.assign({'Content-Type':'application/json'},opts.headers||{});if(state&&state.visitor){opts.headers['X-Visitor-Token']=state.visitor}return fetch(api+path,opts)}
async function detail(r,fallback){try{var j=await r.json();return (j&&j.detail&&typeof j.detail==='string')?j.detail:fallback}catch(e){return fallback}}
async function restore(){greet();if(!state)return;try{var r=await req('/chats/'+encodeURIComponent(state.chat));if(r.status===404||r.status===401){save(null);return}
if(!r.ok)return;var j=await r.json();(j.messages||[]).forEach(function(m){add(m.role,m.content)});if(j.running){var s=status('Still answering… refresh in a moment.');}}catch(e){}}
async function ensureChat(){if(state)return true;var r=await req('/chats',{method:'POST'});if(!r.ok){add('error',await detail(r,'Could not start a chat.'));return false}var j=await r.json();save({chat:j.chat_id,visitor:j.visitor_token});return true}
async function submit(text){if(busy)return;setBusy(true);add('user',text);var thinking=status('…');var reply=null,acc='';
try{if(!(await ensureChat())){thinking.remove();return}
var r=await req('/chats/'+encodeURIComponent(state.chat)+'/messages',{method:'POST',body:JSON.stringify({message:text})});
if(r.status===404||r.status===401){save(null);thinking.remove();add('error','That chat ended. Send your message again to start a new one.');return}
if(!r.ok||!r.body){thinking.remove();add('error',await detail(r,'Could not send that. Try again.'));return}
var reader=r.body.getReader(),dec=new TextDecoder(),buf='';
while(true){var x=await reader.read();if(x.done)break;buf+=dec.decode(x.value,{stream:true});var parts=buf.split(/\\r?\\n\\r?\\n/);buf=parts.pop();
parts.forEach(function(chunk){var data=chunk.split(/\\r?\\n/).filter(function(l){return l.indexOf('data:')===0}).map(function(l){return l.slice(5).replace(/^ /,'')}).join('\\n');if(!data)return;var ev;try{ev=JSON.parse(data)}catch(e){return}
if(ev.type==='text'){acc+=ev.content;if(!reply){thinking.remove();reply=add('assistant','')}reply.innerHTML=fmt(acc.replace(/<think>[\\s\\S]*?(<\\/think>|$)/g,''));down()}
else if(ev.type==='tool'&&ev.status==='started'){thinking.textContent='Looking that up…'}
else if(ev.type==='done'){thinking.remove();if(!reply){reply=add('assistant','')}reply.innerHTML=fmt(ev.content||acc||'(No answer.)');down()}
else if(ev.type==='error'){thinking.remove();if(reply){reply.remove();reply=null}add('error',ev.content||'Something went wrong.')}})}
if(thinking.parentNode){thinking.remove()}}
catch(e){if(thinking.parentNode)thinking.remove();add('error','Could not reach the assistant. Check your connection and try again.')}
finally{setBusy(false);box.focus()}}
document.getElementById('form').addEventListener('submit',function(e){e.preventDefault();var t=box.value.trim();if(!t)return;box.value='';grow();submit(t)});
box.addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();document.getElementById('form').requestSubmit()}});
function grow(){box.style.height='auto';box.style.height=Math.min(box.scrollHeight,140)+'px'}
box.addEventListener('input',grow);
document.getElementById('new').addEventListener('click',function(){if(busy)return;save(null);greet();box.focus()});
restore();
})();
</script>
</body>
</html>
"""


_WIDGET_JS = """(function(){
if(window.__cognitrixChat)return;window.__cognitrixChat=true;
var C={{CONFIG}};
var open=false,frame=null;
var b=document.createElement('button');
b.type='button';b.setAttribute('aria-label','Chat with '+C.title);b.title=C.title;
b.style.cssText='position:fixed;right:20px;bottom:20px;z-index:2147483646;width:56px;height:56px;border-radius:50%;border:0;background:#c2410c;color:#fff;box-shadow:0 6px 24px rgba(0,0,0,.25);cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0';
var chat='<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
var close='<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
b.innerHTML=chat;
function place(){if(!frame)return;var small=window.innerWidth<480;frame.style.cssText='position:fixed;z-index:2147483647;border:0;background:#fff;box-shadow:0 12px 40px rgba(0,0,0,.25);'+(small?'left:0;right:0;top:0;bottom:0;width:100%;height:100%;border-radius:0':'right:20px;bottom:88px;width:380px;height:min(600px,calc(100vh - 120px));border-radius:14px')+(open?'':';display:none')}
function toggle(){open=!open;if(open&&!frame){frame=document.createElement('iframe');frame.src=C.src;frame.title=C.title;frame.setAttribute('allow','clipboard-write');document.body.appendChild(frame)}
place();b.innerHTML=open?close:chat;b.setAttribute('aria-expanded',String(open));if(open&&window.innerWidth<480){b.style.zIndex='2147483647'}else{b.style.zIndex='2147483646'}}
b.addEventListener('click',toggle);
window.addEventListener('resize',place);
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&open)toggle()});
function mount(){document.body.appendChild(b)}
if(document.body)mount();else document.addEventListener('DOMContentLoaded',mount);
})();
"""
