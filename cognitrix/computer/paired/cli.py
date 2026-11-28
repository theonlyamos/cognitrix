"""``cognitrix connect``: pair this PC with a Cognitrix web server and keep it
connected, for machines without the desktop app.

    cognitrix connect cognitrix.example.com --code K7QF-29XM --folder ~/code/app
    cognitrix connect --find      # Cognitrix servers on this PC's tailnet
    cognitrix connect studio.tail1234.ts.net --ask --folder ~/code/app
                                  # no code: approve this PC on the web instead
    cognitrix connect             # reconnect with the saved link
    cognitrix connect --status    # show the saved link
    cognitrix connect --unpair    # forget this PC on the server and here
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

from cognitrix.computer.paired import APPS, COMMANDS, FILES


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        'connect', help='Let your agents on a Cognitrix web server work on this PC (pair it, then stay connected)')
    p.add_argument('server', nargs='?', default='', help='The web server, like cognitrix.example.com')
    p.add_argument('--code', default='', help='A code from the web: Settings, Sign-ins, Get a code (signs this PC in and links it), '
                   'or Settings, Computers, Pair a computer')
    p.add_argument('--find', action='store_true', help="List the Cognitrix servers on this PC's tailnet and exit")
    p.add_argument('--ask', action='store_true',
                   help='Pair without a code: this PC asks over the tailnet and you approve it on the web '
                        '(Settings, Computers). Needs your Tailscale login linked to your account there')
    p.add_argument('--folder', action='append', default=[], help='A folder agents may use (repeat for more)')
    p.add_argument('--read-only', action='store_true', help='Agents may read the folders but not change them')
    p.add_argument('--no-commands', action='store_true', help="Don't let agents run commands here")
    p.add_argument('--apps', action='store_true',
                   help='Also let agents work in app windows on this screen (in the background; opening an app asks you)')
    p.add_argument('--pc-name', default='', help='What to call this PC on the web (default: its host name)')
    p.add_argument('--yes', '-y', action='store_true', help="Don't ask before allowing")
    p.add_argument('--status', action='store_true', help='Show the saved link and exit')
    p.add_argument('--unpair', action='store_true', help='Unpair this PC and forget the link')
    p.set_defaults(func=command)


def _print_link(view: dict[str, Any]) -> None:
    print(f"Server:  {view.get('server')}")
    print(f"Account: {view.get('account')}")
    print(f"This PC: {view.get('name')}")
    print('Folders: ' + (', '.join(f"{f['path']}{'' if f.get('writable', True) else ' (read only)'}"
                                   for f in view.get('folders') or []) or 'none'))
    print('Allowed: ' + (', '.join(view.get('powers') or []) or 'nothing'))
    if view.get('token_kept_in'):
        print(f"Token:   kept in {view['token_kept_in']}")


async def command(args) -> None:
    from cognitrix.computer.paired.connector import (
        LinkError,
        check_folders,
        default_name,
        get_connector,
        link,
        load_config,
        preview,
        public_config,
    )

    if args.status:
        view = public_config()
        if not view['linked']:
            print('This PC is not paired with a web server. Run: cognitrix connect <server> --code <code>')
            return
        _print_link(view)
        return

    if args.find:
        from cognitrix.computer.paired import tailnet
        try:
            found = await tailnet.find_servers()
        except LookupError as exc:
            sys.exit(str(exc))
        if not found:
            print('No Cognitrix server answered on your tailnet. Is its Tailscale private address on '
                  '(Settings, Tailscale)?')
            return
        for item in found:
            print(f"{item['name']}: {item['server']} (Cognitrix {item['version']})"
                  + ('  can pair without a code: add --ask' if item['ask_to_pair'] else ''))
        return

    connector = get_connector()
    if args.unpair:
        if not load_config().get('token'):
            print('This PC is not paired.')
            return
        connector.start()
        for _ in range(40):
            if connector.status == 'connected':
                break
            await asyncio.sleep(0.5)
        await connector.unpair()
        print('Unpaired. The server and this PC have both forgotten the link.')
        return

    asked = None
    if args.ask and not args.code:
        if not args.server:
            sys.exit('Give the server too: cognitrix connect <server> --ask (find it with cognitrix connect --find)')
        from cognitrix.computer.paired.connector import ask, ask_status, link_approved
        try:
            folders = check_folders([{'path': f, 'writable': not args.read_only} for f in args.folder])
            if not folders:
                sys.exit('Share at least one folder with --folder, like --folder ~/code/my-app')
            asked = await ask(args.server, name=args.pc_name or default_name())
        except LinkError as exc:
            sys.exit(exc.message)
        print(f"Asked {asked['server']}. Now approve this PC on the web: Settings, Computers "
              f"(account {asked['account']}). Waiting up to 10 minutes...")
        status = 'pending'
        while status == 'pending':
            await asyncio.sleep(3)
            try:
                status = (await ask_status(asked))['status']
            except LinkError as exc:
                sys.exit(exc.message)
        if status != 'approved':
            sys.exit('It was declined on the web.' if status == 'denied' else 'Nobody approved it in time. Ask again.')
        info = {'account': asked['account'], 'server': asked['server'], 'code': ''}

    if args.code or asked:
        if not args.server:
            sys.exit('Give the server too: cognitrix connect <server> --code <code>')
        try:
            folders = check_folders([{'path': f, 'writable': not args.read_only} for f in args.folder])
            if not asked:
                info = await preview(args.server, args.code)
        except LinkError as exc:
            sys.exit(exc.message)
        if not folders:
            sys.exit('Share at least one folder with --folder, like --folder ~/code/my-app')
        powers = [FILES] + ([] if args.no_commands else [COMMANDS]) + ([APPS] if args.apps else [])
        name = args.pc_name or default_name()
        print(f"Let agents of {info['account']} on {info['server']} use this PC ({name})?")
        for f in folders:
            print(f"  {f['path']} ({'read & write' if f['writable'] else 'read only'})")
        print('  They can: read' + ('' if args.read_only else ' and edit') + ' files there'
              + ('' if args.no_commands else ', and run commands (each one asks you in the chat first)')
              + (', and use apps on this screen' if args.apps else ''))
        if not args.yes:
            answer = input('Allow? [y/N] ').strip().lower()
            if answer not in ('y', 'yes'):
                print('Nothing was shared.')
                return
        try:
            if asked:
                view = await link_approved(asked, name=name, folders=folders, powers=powers)
            else:
                view = await link(info['server'], info['code'], name=name, folders=folders, powers=powers)
        except LinkError as exc:
            sys.exit(exc.message)
        print('Paired.')
        _print_link(view)
    elif not load_config().get('token'):
        sys.exit('This PC is not paired yet. Make a code on the web (Settings, Sign-ins, Get a code), '
                 'then run: cognitrix connect <server> --code <code> --folder <path>')

    print('Connecting... (Ctrl+C to stop; agents can only use this PC while this runs)')
    last = ''
    task = asyncio.create_task(connector.run_forever())
    try:
        while not task.done():
            status = connector.status + (f' ({connector.last_error})' if connector.last_error and connector.status != 'connected' else '')
            if status != last:
                print(f'  {status}')
                last = status
            await asyncio.sleep(1)
    finally:
        await connector.stop()
    if connector.status == 'unpaired':
        print('This PC was unpaired. Pair it again with a new code.')
