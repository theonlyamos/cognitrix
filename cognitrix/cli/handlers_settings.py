"""`cognitrix settings`: app settings from the command line.

For servers without a browser at hand, a Docker Compose file that points the
app at its Redis container, and the way back in when a database change keeps
the server from starting (`cognitrix settings set DB_TYPE=sqlite`).
"""
from __future__ import annotations

import sys
from argparse import Namespace

from cognitrix import app_settings


def manage_settings(args: Namespace) -> None:
    action = getattr(args, 'settings_action', None) or 'list'
    if action == 'list':
        values = app_settings._values()
        for spec in app_settings.registry().values():
            if args.page and spec.page != args.page:
                continue
            value = values.get(spec.name, '')
            if spec.kind == 'secret':
                shown = '(set)' if value else ''
            else:
                shown = value
            print(f'{spec.name}={shown}')
        return
    changes: dict[str, str] = {}
    for item in args.items:
        if action == 'unset':
            changes[item.strip()] = ''
            continue
        if '=' not in item:
            print(f'Write each setting as NAME=VALUE (got "{item}").')
            sys.exit(1)
        name, value = item.split('=', 1)
        name = name.strip()
        if args.if_unset and app_settings._values().get(name):
            continue
        changes[name] = value
    if not changes:
        return
    try:
        app_settings.set_many(changes)
    except ValueError as exc:
        print(exc)
        sys.exit(1)
    for name in changes:
        print(f'{"Cleared" if action == "unset" else "Saved"} {name}')


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser('settings', help='Show or change app settings (the ones in Settings)')
    inner = parser.add_subparsers(dest='settings_action')
    listing = inner.add_parser('list', help='Show saved settings (secrets only say whether they are set)')
    listing.add_argument('--page', default='', help='Only one Settings section, e.g. advanced')
    setting = inner.add_parser('set', help='Save settings: NAME=VALUE ...')
    setting.add_argument('items', nargs='+', metavar='NAME=VALUE')
    setting.add_argument('--if-unset', action='store_true', help='Leave settings that already have a value')
    clearing = inner.add_parser('unset', help='Clear settings back to their defaults: NAME ...')
    clearing.add_argument('items', nargs='+', metavar='NAME')
    parser.set_defaults(func=manage_settings, page='', items=[], if_unset=False)
