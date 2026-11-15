"""A small MCP server whose tools have a screen (MCP Apps), for tests and
for trying the chat's app panel by hand:

    python tests/fixtures/mcp_apps_demo.py            # stdio
    python tests/fixtures/mcp_apps_demo.py --http 8765  # streamable HTTP at /mcp

``show_board`` and ``add_task`` open a task board; ``toggle_task`` is only
for the board itself (visibility ``app``); ``clear_board`` is destructive.
"""
from __future__ import annotations

import sys
from typing import Any

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

BOARD = {'title': 'Tasks', 'tasks': []}

HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Task board</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#16181a;--line:#e3e3df;--accent:#4e6b00}
:root[data-theme=dark]{--bg:#141619;--fg:#f4f4f2;--line:#2a2d31;--accent:#c6f24e}
body{margin:0;font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
main{padding:20px}
h1{font-size:20px;margin:0 0 12px}
ul{list-style:none;padding:0;margin:0}
li{display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)}
li.done span{text-decoration:line-through;opacity:.6}
form{display:flex;gap:8px;margin-top:14px}
input[type=text]{flex:1;padding:8px;border:1px solid var(--line);border-radius:6px;background:transparent;color:inherit}
button{padding:8px 12px;border:1px solid var(--line);border-radius:6px;background:transparent;color:inherit;cursor:pointer}
#status{font-size:12px;opacity:.7;margin-top:10px}
</style></head>
<body><main>
<h1 id="title">Loading…</h1>
<ul id="list"></ul>
<form id="add"><input type="text" id="text" placeholder="New task"><button>Add</button></form>
<div style="display:flex;gap:8px;margin-top:10px"><button id="ask">Ask the agent to plan</button><button id="clear">Clear</button></div>
<div id="status"></div>
</main>
<script>
var seq = 0, pending = {};
function send(msg){ window.parent.postMessage(Object.assign({jsonrpc: '2.0'}, msg), '*'); }
function request(method, params){
  return new Promise(function(resolve, reject){
    var id = ++seq; pending[id] = {resolve: resolve, reject: reject};
    send({id: id, method: method, params: params || {}});
  });
}
function status(t){ document.getElementById('status').textContent = t; }
function draw(board){
  if (!board) return;
  document.getElementById('title').textContent = board.title;
  var list = document.getElementById('list'); list.innerHTML = '';
  board.tasks.forEach(function(t, i){
    var li = document.createElement('li'); if (t.done) li.className = 'done';
    var box = document.createElement('input'); box.type = 'checkbox'; box.checked = t.done;
    box.onchange = function(){ call('toggle_task', {index: i}); };
    var s = document.createElement('span'); s.textContent = t.text;
    li.appendChild(box); li.appendChild(s); list.appendChild(li);
  });
  request('ui/update-model-context', {structuredContent: {open_tasks: board.tasks.filter(function(t){return !t.done;}).length}});
}
function call(name, args){
  status('Running ' + name + '…');
  return request('tools/call', {name: name, arguments: args}).then(function(r){
    status(r.isError ? 'Failed' : ''); draw(r.structuredContent);
  }, function(e){ status('Error: ' + e.message); });
}
window.addEventListener('message', function(e){
  if (e.source !== window.parent) return;
  var m = e.data || {};
  if (m.id && pending[m.id] && (m.result !== undefined || m.error)) {
    var p = pending[m.id]; delete pending[m.id];
    if (m.error) p.reject(new Error(m.error.message)); else p.resolve(m.result);
    return;
  }
  if (m.method === 'ui/notifications/tool-result') draw(m.params.structuredContent);
  if (m.method === 'ui/notifications/host-context-changed' && m.params.theme) document.documentElement.dataset.theme = m.params.theme;
  if (m.method === 'ui/resource-teardown') send({id: m.id, result: {}});
});
document.getElementById('add').onsubmit = function(ev){
  ev.preventDefault(); var t = document.getElementById('text');
  if (t.value.trim()) call('add_task', {text: t.value.trim()}); t.value = '';
};
document.getElementById('clear').onclick = function(){ call('clear_board', {}); };
document.getElementById('ask').onclick = function(){
  request('ui/message', {role: 'user', content: [{type: 'text', text: 'Look at my task board and suggest what to do first.'}]});
};
request('ui/initialize', {protocolVersion: '2026-01-26', appCapabilities: {}, clientInfo: {name: 'task-board', version: '1'}})
  .then(function(r){
    document.documentElement.dataset.theme = (r.hostContext || {}).theme || 'light';
    send({method: 'ui/notifications/initialized', params: {}});
  });
</script></body></html>
"""

apps = Apps()
URI = 'ui://tasks/board.html'


def _board() -> dict[str, Any]:
    return {'title': BOARD['title'], 'tasks': [dict(t) for t in BOARD['tasks']]}


@apps.tool(resource_uri=URI, description='Show the task board to the person.',
           annotations=ToolAnnotations(readOnlyHint=True), structured_output=True)
def show_board(title: str = '') -> dict[str, Any]:
    if title:
        BOARD['title'] = title
    return _board()


@apps.tool(resource_uri=URI, description='Add a task to the board and show it.', structured_output=True)
def add_task(text: str) -> dict[str, Any]:
    BOARD['tasks'].append({'text': text, 'done': False})
    return _board()


@apps.tool(resource_uri=URI, visibility=['app'], description='Tick or untick a task.', structured_output=True)
def toggle_task(index: int) -> dict[str, Any]:
    if 0 <= index < len(BOARD['tasks']):
        BOARD['tasks'][index]['done'] = not BOARD['tasks'][index]['done']
    return _board()


@apps.tool(resource_uri=URI, description='Remove every task.',
           annotations=ToolAnnotations(destructiveHint=True), structured_output=True)
def clear_board() -> dict[str, Any]:
    BOARD['tasks'].clear()
    return _board()


apps.add_html_resource(URI, HTML, title='Task board', prefers_border=False)
server = MCPServer('task-board', extensions=[apps])

if __name__ == '__main__':
    if '--http' in sys.argv:
        port = int(sys.argv[sys.argv.index('--http') + 1])
        server.run(transport='streamable-http', port=port)
    else:
        server.run()
