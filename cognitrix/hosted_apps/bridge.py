"""``window.cognitrix`` inside an app: what an app may ask the person for.

An app's pages are served sandboxed into an opaque origin (see ``APP_CSP`` in
``api/routes/hosted_apps.py``), so they can't call Cognitrix themselves. The
proxy puts this small runtime at the top of every HTML page it serves; it
talks to the Cognitrix page around the app (``/apps/open/<slug>`` or the
app's page) with ``postMessage``. That page asks the person the first time,
remembers their answer (``store.set_permission``), and does the work:

- ``cognitrix.notify(title, body)``: a system notification (the desktop
  app's, which opens the app when clicked; the browser's on the web);
- ``cognitrix.tray.set({tooltip, title, items}) / clear() / onClick(fn)``:
  the app's own tray (menu bar) icon, for apps installed in the desktop app;
- ``cognitrix.apps.list() / call(app, tool, args)``: the person's connected
  apps. Reading runs; a tool that changes something asks the person each time;
- ``cognitrix.permissions.query(power) / request(power)``, and
  ``cognitrix.host()``: where the app is shown (desktop or web, installed).

Calls reject when nothing answers within a few seconds, for example when the
app's link was opened outside Cognitrix.
"""
from __future__ import annotations

import re

RUNTIME = r"""
(function(){
  if (window.cognitrix && window.cognitrix.__app) return;
  var pending = {}, seq = 0, host = null, waiting = [], trayFns = [];
  var inFrame = window.parent && window.parent !== window;
  function hello(){ if (inFrame) window.parent.postMessage({cognitrix: 1, type: 'hello'}, '*'); }
  function whenHost(){
    if (host) return Promise.resolve(host);
    return new Promise(function(resolve, reject){
      if (!inFrame) { reject(new Error('Open this app from Cognitrix to use this.')); return; }
      var t = setTimeout(function(){ reject(new Error('Cognitrix is not answering here.')); }, 5000);
      waiting.push(function(h){ clearTimeout(t); resolve(h); });
      hello();
    });
  }
  function call(kind, payload){
    return whenHost().then(function(){
      return new Promise(function(resolve, reject){
        var id = 'k' + (++seq);
        pending[id] = {resolve: resolve, reject: reject};
        window.parent.postMessage({cognitrix: 1, type: 'call', id: id, kind: kind, payload: payload || {}}, '*');
      });
    });
  }
  window.addEventListener('message', function(e){
    if (!inFrame || e.source !== window.parent) return;
    var m = e.data || {};
    if (!m || m.cognitrix !== 1) return;
    if (m.type === 'host') {
      host = {platform: m.platform === 'desktop' ? 'desktop' : 'web', installed: !!m.installed, window: !!m.window};
      var w = waiting; waiting = []; w.forEach(function(f){ f(host); });
    } else if (m.type === 'result' && pending[m.id]) {
      var p = pending[m.id]; delete pending[m.id];
      if (m.error) p.reject(new Error(String(m.error))); else p.resolve(m.value);
    } else if (m.type === 'tray-click') {
      trayFns.forEach(function(f){ try { f(String(m.id || '')); } catch (err) { setTimeout(function(){ throw err; }); } });
    }
  });
  hello();
  window.cognitrix = {
    __app: true,
    host: function(){ return whenHost(); },
    notify: function(title, body){ return call('notify', {title: String(title || ''), body: String(body == null ? '' : body)}); },
    permissions: {
      query: function(power){ return call('permission.query', {power: String(power || '')}); },
      request: function(power){ return call('permission.request', {power: String(power || '')}); }
    },
    tray: {
      set: function(options){
        var o = options || {};
        var items = (o.items || []).map(function(i){ return {id: String(i && i.id || ''), label: String(i && i.label || '')}; });
        return call('tray.set', {tooltip: String(o.tooltip || ''), title: String(o.title || ''), items: items});
      },
      clear: function(){ return call('tray.clear', {}); },
      onClick: function(fn){
        if (typeof fn === 'function') trayFns.push(fn);
        return function(){ trayFns = trayFns.filter(function(f){ return f !== fn; }); };
      }
    },
    apps: {
      list: function(){ return call('apps.list', {}); },
      call: function(app, tool, args){ return call('apps.call', {app: String(app || ''), tool: String(tool || ''), arguments: args || {}}); }
    }
  };
})();
"""

SCRIPT = ('<script data-cognitrix-runtime>' + RUNTIME.strip() + '</script>').encode()
MAX_HTML = 5 * 1024 * 1024

_HEAD = re.compile(rb'<head(\s[^>]*)?>', re.I)
_HTML = re.compile(rb'<html(\s[^>]*)?>', re.I)
_DOCTYPE = re.compile(rb'^\s*<!doctype[^>]*>', re.I)


def inject(body: bytes) -> bytes:
    """The page with the runtime first in its <head> (or as early as is safe)."""
    if len(body) > MAX_HTML or b'data-cognitrix-runtime' in body[:4096]:
        return body
    window = body[:65536]
    for pattern in (_HEAD, _HTML, _DOCTYPE):
        match = pattern.search(window)
        if match:
            return body[:match.end()] + SCRIPT + body[match.end():]
    return SCRIPT + body
