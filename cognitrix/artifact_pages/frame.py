"""The document an artifact page is shown in.

A page runs inside a sandboxed iframe served by ``/artifact-frame/...``: the
response's own Content-Security-Policy gives it an opaque origin (``sandbox``
without ``allow-same-origin``), so a page can never read the app's storage,
call the API as the viewer, or reach other websites (``connect-src 'none'``).
It may load scripts from a few public CDNs and fonts from Google Fonts.

A small runtime is put in front of the page's own code. It talks to the
Cognitrix window around it with ``postMessage``:

- ``window.cognitrix.ask/data/download/apps``: the page powers. The window
  checks the page declared the power and the viewer allowed it, then calls
  the API on the viewer's behalf and posts back the answer.
- comment mode: the window asks for a click, the runtime answers with where
  on the page it landed and the text there; the window sends the comment
  pins to draw.
- the page's height and theme.
"""
from __future__ import annotations

import html
import json
import os
from datetime import UTC, datetime, timedelta

import jwt

from cognitrix.common.constants import JWT_ALGORITHM, JWT_SECRET_KEY

TOKEN_MINUTES = 120
_AUDIENCE = 'cognitrix-artifact-frame'
CDN_HOSTS = (
    'https://cdnjs.cloudflare.com', 'https://cdn.jsdelivr.net', 'https://unpkg.com',
    'https://cdn.tailwindcss.com', 'https://code.jquery.com',
)
MARKED_URL = 'https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js'


def _key() -> str:
    # A key of its own: a frame token is never a sign-in token, and the
    # other way round.
    return f'{JWT_SECRET_KEY}:artifact-frame'


def frame_token(page_id: str, version: int, viewer: str) -> str:
    payload = {
        'p': str(page_id), 'v': int(version), 'u': str(viewer), 'aud': _AUDIENCE,
        'exp': datetime.now(UTC) + timedelta(minutes=TOKEN_MINUTES),
    }
    return jwt.encode(payload, _key(), algorithm=JWT_ALGORITHM)


def read_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _key(), algorithms=[JWT_ALGORITHM], audience=_AUDIENCE)
    except jwt.PyJWTError:
        return None


def _ancestors() -> str:
    """Who may show a page in a frame: the app itself, the origins the API
    already trusts for the web app (a dev server), and the desktop window."""
    from cognitrix.config import settings

    allowed = ["'self'"]
    allowed += [o for o in settings.cors_origins if o.startswith(('http://', 'https://')) and ' ' not in o and ';' not in o]
    if os.environ.get('COGNITRIX_DESKTOP') == '1':
        allowed += ['tauri://localhost', 'http://tauri.localhost', 'https://tauri.localhost']
    return ' '.join(dict.fromkeys(allowed))


def content_security_policy() -> str:
    cdns = ' '.join(CDN_HOSTS)
    return (
        "default-src 'none'; "
        f"script-src 'unsafe-inline' 'unsafe-eval' {cdns}; "
        f"style-src 'unsafe-inline' https://fonts.googleapis.com {cdns}; "
        f"font-src data: https://fonts.gstatic.com {cdns}; "
        "img-src data: blob:; media-src data: blob:; "
        "connect-src 'none'; frame-src 'none'; worker-src blob:; "
        "form-action 'none'; base-uri 'none'; "
        f"frame-ancestors {_ancestors()}; "
        "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-modals allow-forms"
    )


RUNTIME = r"""
(function(){
  var pending = {}, seq = 0, picking = false, pinLayer = null;
  function call(kind, payload){
    return new Promise(function(resolve, reject){
      var id = 'c' + (++seq);
      pending[id] = {resolve: resolve, reject: reject};
      parent.postMessage({cognitrix: 1, type: 'call', id: id, kind: kind, payload: payload || {}}, '*');
    });
  }
  window.addEventListener('message', function(e){
    if (e.source !== parent) return;
    var m = e.data || {};
    if (!m.cognitrix) return;
    if (m.type === 'result' && pending[m.id]) {
      var p = pending[m.id]; delete pending[m.id];
      if (m.error) p.reject(new Error(m.error)); else p.resolve(m.value);
    } else if (m.type === 'pick') {
      picking = !!m.on;
      document.documentElement.style.cursor = picking ? 'crosshair' : '';
    } else if (m.type === 'pins') {
      drawPins(m.pins || []);
    } else if (m.type === 'theme') {
      document.documentElement.dataset.cognitrixTheme = m.theme;
    }
  });
  function size(){
    var d = document.documentElement;
    return {w: Math.max(d.scrollWidth, 1), h: Math.max(d.scrollHeight, document.body ? document.body.scrollHeight : 0, 1)};
  }
  document.addEventListener('click', function(e){
    if (!picking) return;
    e.preventDefault(); e.stopPropagation();
    var s = size();
    var el = e.target;
    var text = (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim().slice(0, 160);
    picking = false;
    document.documentElement.style.cursor = '';
    parent.postMessage({cognitrix: 1, type: 'picked', anchor: {x: e.pageX / s.w, y: e.pageY / s.h, text: text}}, '*');
  }, true);
  function drawPins(pins){
    if (!document.body) return;
    if (!pinLayer) {
      pinLayer = document.createElement('div');
      pinLayer.setAttribute('aria-hidden', 'true');
      pinLayer.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483646';
      document.body.appendChild(pinLayer);
      if (getComputedStyle(document.body).position === 'static') document.body.style.position = 'relative';
    }
    pinLayer.innerHTML = '';
    var s = size();
    pins.forEach(function(p){
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = p.n;
      b.title = p.title || '';
      b.style.cssText = 'position:absolute;width:22px;height:22px;margin:-22px 0 0 0;border-radius:50% 50% 50% 4px;border:0;' +
        'background:' + (p.active ? '#c6f24e' : '#16181a') + ';color:' + (p.active ? '#0b0c0e' : '#f4f4f2') + ';font:600 11px ui-monospace,monospace;' +
        'box-shadow:0 2px 8px rgba(0,0,0,.3);cursor:pointer;left:' + (p.x * s.w) + 'px;top:' + (p.y * s.h) + 'px';
      b.addEventListener('click', function(ev){ ev.stopPropagation(); parent.postMessage({cognitrix: 1, type: 'pin', id: p.id}, '*'); });
      pinLayer.appendChild(b);
    });
  }
  var lastH = 0;
  function report(){
    var h = size().h;
    if (Math.abs(h - lastH) > 2) { lastH = h; parent.postMessage({cognitrix: 1, type: 'size', height: h}, '*'); }
  }
  window.addEventListener('load', function(){ report(); parent.postMessage({cognitrix: 1, type: 'ready'}, '*'); });
  setInterval(report, 1000);
  var json = function(v){ return JSON.stringify(v === undefined ? null : v); };
  var parse = function(t){ try { return t == null ? null : JSON.parse(t); } catch (e) { return t; } };
  window.cognitrix = {
    ask: function(prompt){ return call('ask', {prompt: String(prompt || '')}); },
    data: {
      get: function(key, opts){ return call('data.get', {key: key, shared: !!(opts && opts.shared)}).then(parse); },
      set: function(key, value, opts){ return call('data.set', {key: key, value: json(value), shared: !!(opts && opts.shared)}); },
      remove: function(key, opts){ return call('data.remove', {key: key, shared: !!(opts && opts.shared)}); },
      list: function(opts){ return call('data.list', {shared: !!(opts && opts.shared)}).then(function(all){
        var out = {}; Object.keys(all || {}).forEach(function(k){ out[k] = parse(all[k]); }); return out; }); }
    },
    download: function(filename, content, mimeType){
      return call('download', {filename: String(filename || 'download.txt'), content: String(content == null ? '' : content), mime: String(mimeType || 'text/plain')});
    },
    apps: {
      list: function(){ return call('apps.list', {}); },
      call: function(app, tool, args){ return call('apps.call', {app: app, tool: tool, arguments: args || {}}); }
    }
  };
})();
"""

BASE_STYLE = """
:root{color-scheme:light dark}
html,body{margin:0}
body{font-family:'Space Grotesk',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;line-height:1.6;background:#fff;color:#16181a}
@media (prefers-color-scheme:dark){body{background:#141619;color:#f4f4f2}}
"""

MARKDOWN_STYLE = """
.md{max-width:760px;margin:0 auto;padding:28px 22px 60px;font-size:15.5px}
.md h1{font-size:28px;line-height:1.25;margin:0 0 14px}
.md h2{font-size:21px;margin:30px 0 10px}
.md h3{font-size:17px;margin:24px 0 8px}
.md a{color:#4e6b00}
.md code{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:.88em;background:rgba(127,127,127,.15);padding:1px 5px;border-radius:4px}
.md pre{background:rgba(127,127,127,.12);padding:14px 16px;border-radius:8px;overflow:auto}
.md pre code{background:none;padding:0}
.md table{border-collapse:collapse;width:100%;font-size:14px;margin:14px 0}
.md th,.md td{border-bottom:1px solid rgba(127,127,127,.25);padding:7px 9px;text-align:left}
.md blockquote{margin:14px 0;padding:2px 16px;border-left:3px solid #c6f24e;color:inherit;opacity:.85}
.md img{max-width:100%}
.md .raw{white-space:pre-wrap;font-family:inherit}
@media (prefers-color-scheme:dark){.md a{color:#c6f24e}}
"""


def _runtime_tag() -> str:
    return f'<script>{RUNTIME}</script>'


def render(source: str, fmt: str, title: str) -> str:
    """The full document for one version of a page."""
    safe_title = html.escape(title or 'Artifact')
    if fmt == 'markdown':
        payload = json.dumps(source).replace('</', '<\\/')
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{safe_title}</title><style>{BASE_STYLE}{MARKDOWN_STYLE}</style>'
            f'{_runtime_tag()}<script src="{MARKED_URL}"></script></head>'
            '<body><article class="md" id="md"></article>'
            f'<script>(function(){{var src={payload};var el=document.getElementById("md");'
            'if(window.marked){el.innerHTML=window.marked.parse(src,{mangle:false,headerIds:false});'
            'el.querySelectorAll("script").forEach(function(s){s.remove();});}'
            'else{var p=document.createElement("pre");p.className="raw";p.textContent=src;el.appendChild(p);}'
            '})();</script></body></html>'
        )
    text = source.lstrip()
    lowered = text[:400].lower()
    if '<html' in lowered or lowered.startswith('<!doctype'):
        # Put the runtime first in <head> so it is there before the page's scripts run.
        index = lowered.find('<head')
        if index != -1:
            close = text.find('>', index)
            if close != -1:
                return text[:close + 1] + '<meta charset="utf-8">' + _runtime_tag() + text[close + 1:]
        index = lowered.find('<html')
        close = text.find('>', index)
        if close != -1:
            return text[:close + 1] + '<head><meta charset="utf-8">' + _runtime_tag() + '</head>' + text[close + 1:]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{safe_title}</title><style>{BASE_STYLE}</style>{_runtime_tag()}</head>'
        f'<body>{source}</body></html>'
    )
