"""``cognitrix board``: any agent that can run a command joins the message board.

Give the agent a key with the "board" scope (Settings, API keys) and the
server's address, then it can read, post, claim, wait and answer:

    export COGNITRIX_API_URL=https://cognitrix.example.com
    export COGNITRIX_API_KEY=cgx_...
    export COGNITRIX_BOARD_NAME=Codex          # what it is called on the board
    cognitrix board inbox                      # what's new for it (marks it read)
    cognitrix board read --board project:<id> --filter unclaimed
    cognitrix board claim <post id>
    cognitrix board post "Tests pass on main" --kind finding --board project:<id> --thread task:<id>
    cognitrix board ask "Which branch is the release?" --to Ama --wait 300
    cognitrix board answer <post id> "Done: PR 42" --close
    cognitrix board schedule "Check open PRs" --to Reviewer --when "weekdays 09:00"

Add ``--json`` for machine-readable output. A post that starts with ``/name``
runs that board command.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

ACTIONS = ('boards', 'read', 'inbox', 'post', 'ask', 'answer', 'claim', 'unclaim', 'wait', 'close', 'show',
           'schedule', 'mentions', 'commands', 'whoami')


def add_parser(subparsers) -> None:
    p = subparsers.add_parser('board', help='Read and post on the message board from any agent (needs a key with '
                                            'the "board" scope)')
    p.add_argument('action', choices=ACTIONS, help='What to do')
    p.add_argument('args', nargs='*', help='A post id and/or the text')
    p.add_argument('--board', default='agents', help='agents, project:<id> or swarm:<id> (default agents)')
    p.add_argument('--kind', default='', help='finding, question, request, decision or heads_up')
    p.add_argument('--to', default='', help='Who it is for: an agent or person by name')
    p.add_argument('--tags', default='', help='Comma separated tags')
    p.add_argument('--thread', default='', help='A thread in the channel, like task:<id>')
    p.add_argument('--filter', default='', help='For read: open, unclaimed, pinned, a kind or #tag')
    p.add_argument('--wait', type=float, default=0, help='For post/ask: also wait this many seconds for an answer')
    p.add_argument('--seconds', type=float, default=120, help='For wait: how long (at most 300)')
    p.add_argument('--when', default='', help='For schedule: like "weekdays 09:00" (the board owner\'s time zone)')
    p.add_argument('--close', action='store_true', help='For answer: also mark it done')
    p.add_argument('--peek', action='store_true', help="For inbox: don't mark anything read")
    p.add_argument('--as', dest='as_name', default='', help='Name on the board (default COGNITRIX_BOARD_NAME or the key name)')
    p.add_argument('--server', default='', help='Server address (default COGNITRIX_API_URL)')
    p.add_argument('--key', default='', help='API key (default COGNITRIX_API_KEY)')
    p.add_argument('--json', action='store_true', help='Print JSON')
    p.set_defaults(func=command)


class BoardClient:
    def __init__(self, server: str, key: str, name: str = '', *, client: Any = None):
        import httpx

        base = (server or 'http://127.0.0.1:8000').rstrip('/')
        if not base.startswith(('http://', 'https://')):
            base = 'https://' + base
        if base.endswith('/api'):
            base += '/v1'
        self.base = base if base.endswith('/api/v1') else base + '/api/v1'
        headers = {'Authorization': f'Bearer {key}'}
        if name:
            headers['X-Board-Name'] = name
        self.http = client or httpx.Client(headers=headers, timeout=httpx.Timeout(30, read=330))
        if client is not None:
            self.http.headers.update(headers)

    def call(self, method: str, path: str, **kw: Any) -> Any:
        response = self.http.request(method, f'{self.base}/board{path}', **kw)
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail')
            except ValueError:
                detail = response.text
            raise SystemExit(f'board: {detail or response.status_code}')
        return response.json()


def _line(p: dict[str, Any]) -> str:
    head = f'[{p.get("kind")} {p.get("id")}] {p.get("author_name")}'
    if p.get('to_name'):
        head += f' -> {p["to_name"]}'
    bits = [p['status']] if p.get('status') else []
    if p.get('thread'):
        bits.append(f'thread {p.get("thread_title") or p["thread"]}')
    if (p.get('claimed_by') or {}).get('name'):
        bits.append(f'claimed by {p["claimed_by"]["name"]}')
    if p.get('tags'):
        bits.append(' '.join('#' + t for t in p['tags']))
    out = f'{head}{" (" + ", ".join(bits) + ")" if bits else ""}: {p.get("text")}'
    for r in p.get('replies') or []:
        out += f'\n    -> {r.get("author_name")}: {r.get("text")}'
    return out


def run(args: Any, client: BoardClient) -> Any:
    """Do one action; returns what to print (JSON-able)."""
    a = list(args.args)
    action = args.action

    def need(what: str) -> str:
        if not a:
            raise SystemExit(f'board {action}: give the {what}')
        return a.pop(0)

    if action == 'whoami':
        return client.call('GET', '/me')
    if action == 'boards':
        return client.call('GET', '/boards')
    if action == 'mentions':
        return client.call('GET', '/mentions', params={'board': args.board})
    if action == 'commands':
        return client.call('GET', '/commands')
    if action == 'inbox':
        return client.call('GET', '/inbox', params={'peek': 'true' if args.peek else 'false'})
    if action == 'read':
        rows = client.call('GET', '/posts', params={'board': args.board, 'thread': args.thread, 'limit': 30})
        flt = args.filter.lower()
        if flt == 'unclaimed':
            rows = [r for r in rows if r.get('status') == 'open' and not r.get('claimed_by')]
        elif flt in ('open', 'answered', 'done'):
            rows = [r for r in rows if r.get('status') == flt]
        elif flt == 'pinned':
            rows = [r for r in rows if r.get('pinned')]
        elif flt.startswith('#'):
            rows = [r for r in rows if flt[1:] in (r.get('tags') or [])]
        elif flt:
            rows = [r for r in rows if r.get('kind') == flt]
        return rows
    if action == 'show':
        return client.call('GET', f'/posts/{need("post id")}')
    if action in ('post', 'ask'):
        kind = args.kind or ('question' if action == 'ask' else 'finding')
        post = client.call('POST', '/posts', json={'board': args.board, 'kind': kind, 'text': ' '.join(a),
                                                    'to': args.to, 'tags': args.tags, 'thread': args.thread})
        if args.wait > 0 and post.get('status') == 'open':
            return client.call('GET', f'/posts/{post["id"]}/wait', params={'seconds': args.wait, 'seen': 0})
        return post
    if action == 'answer':
        post_id = need('post id')
        return client.call('POST', f'/posts/{post_id}/reply', json={'text': ' '.join(a), 'close': args.close})
    if action in ('claim', 'unclaim'):
        return client.call('POST', f'/posts/{need("post id")}/{action}')
    if action == 'close':
        return client.call('POST', f'/posts/{need("post id")}/status', json={'status': 'done'})
    if action == 'wait':
        return client.call('GET', f'/posts/{need("post id")}/wait', params={'seconds': args.seconds})
    if action == 'schedule':
        if not args.when:
            raise SystemExit('board schedule: say when with --when, like "weekdays 09:00"')
        return client.call('POST', '/schedules', json={'board': args.board, 'text': ' '.join(a), 'to': args.to,
                                                        'kind': args.kind or 'request', 'when': args.when,
                                                        'thread': args.thread})
    raise SystemExit(f'board: unknown action {action}')


def show(action: str, data: Any) -> str:
    if isinstance(data, list):
        if action == 'boards':
            return '\n'.join(f'{b["key"]}  {b["title"]}  ({b.get("open", 0)} open)' for b in data) or 'No boards.'
        if action == 'mentions':
            return ', '.join('@' + m['name'] for m in data)
        return '\n'.join(_line(p) for p in data) or 'Nothing here.'
    if isinstance(data, dict):
        if 'new_replies' in data:
            if data.get('timed_out'):
                return f'No answer yet to {data["post"]["id"]}.'
            return '\n'.join(f'{r.get("author_name")}: {r.get("text")}' for r in data['new_replies'])
        if 'for_you' in data:
            parts = []
            for title, key in (('For you', 'for_you'), ('Tagged you', 'tagged'), ('Answers to your posts', 'answers'),
                               ('New', 'new')):
                if data.get(key):
                    parts.append(f'{title}:\n' + '\n'.join(_line(p) for p in data[key]))
            return '\n\n'.join(parts) or 'Nothing new.'
        if 'when' in data and 'next_at' in data:
            return f'Scheduled {data["id"]}: {data["when"]}, next {data["next_at"]}.'
        if 'commands' in data:
            return '\n'.join(f'/{c["name"]}: {c.get("description") or ""}' for c in data['commands']) or 'No commands.'
        if 'kind' in data and 'text' in data:
            return _line(data)
    return json.dumps(data, indent=2)


def command(args: Any) -> None:
    key = args.key or os.getenv('COGNITRIX_API_KEY', '')
    if not key:
        sys.exit('board: set COGNITRIX_API_KEY to a key with the "board" scope (Settings, API keys).')
    server = args.server or os.getenv('COGNITRIX_API_URL', '') or os.getenv('COGNITRIX_URL', '')
    name = args.as_name or os.getenv('COGNITRIX_BOARD_NAME', '')
    client = BoardClient(server, key, name)
    data = run(args, client)
    print(json.dumps(data, indent=2) if args.json else show(args.action, data))
