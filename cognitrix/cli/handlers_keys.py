"""`cognitrix keys`: provider API keys from the command line.

The app keeps model keys in Settings, Models and keys (providers/saved_keys.py)
and never reads them from the environment. On a server, in a container or on
a benchmark machine there may be no window to open Settings in, so this saves
the same keys the desktop app does:

    cognitrix keys list
    cognitrix keys set anthropic            # asks for the key (not echoed)
    echo "$KEY" | cognitrix keys set anthropic --stdin
    cognitrix keys set anthropic --from-env ANTHROPIC_API_KEY
    cognitrix keys import-env               # every usual *_API_KEY variable
    cognitrix keys unset anthropic
"""
from __future__ import annotations

import os
import sys
from argparse import Namespace

# The variable names other tools use for each provider's key.
ENV_NAMES: dict[str, tuple[str, ...]] = {
    'openai': ('OPENAI_API_KEY',),
    'anthropic': ('ANTHROPIC_API_KEY', 'COG_ANTHROPIC_API_KEY'),
    'google': ('GEMINI_API_KEY', 'GOOGLE_API_KEY'),
    'openrouter': ('OPENROUTER_API_KEY',),
    'groq': ('GROQ_API_KEY',),
    'cerebras': ('CEREBRAS_API_KEY',),
    'cohere': ('CO_API_KEY', 'COHERE_API_KEY'),
    'together': ('TOGETHER_API_KEY',),
    'huggingface': ('HUGGINGFACE_ACCESS_TOKEN', 'HF_TOKEN'),
    'clarifai': ('CLARIFAI_ACCESS_TOKEN',),
    'aimlapi': ('AIMLAPI_API_KEY',),
    'minds': ('MINDS_API_KEY',),
    'deepgram': ('DEEPGRAM_API_KEY',),
}


def _providers() -> list[str]:
    from cognitrix.config import CognitrixSettings as Settings

    return sorted(Settings._SAVED_KEY_PROVIDERS)


def _masked(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else '(set)'


def _set(provider: str, key: str) -> None:
    from cognitrix.providers.saved_keys import set_saved_key

    set_saved_key(provider, key)


def manage_keys(args: Namespace) -> None:
    from cognitrix.providers.saved_keys import saved_providers

    action = getattr(args, 'keys_action', None) or 'list'
    if action == 'list':
        saved = saved_providers()
        rows = [(p, _masked(saved[p]) if saved.get(p) else '') for p in _providers()]
        if getattr(args, 'json', False):
            from .utils import print_json

            print_json({p: bool(saved.get(p)) for p in _providers()})
            return
        for provider, shown in rows:
            print(f"{provider:<12} {shown or '-'}")
        return

    if action == 'import-env':
        found = 0
        for provider, names in ENV_NAMES.items():
            value = next((os.environ[n] for n in names if os.environ.get(n)), '')
            if not value:
                continue
            if args.if_unset and saved_providers().get(provider):
                continue
            _set(provider, value)
            found += 1
            print(f"Saved the {provider} key")
        if not found:
            print("No provider key variables found.", file=sys.stderr)
        return

    provider = (args.provider or '').strip().lower()
    if provider not in _providers():
        print(f"Unknown provider '{provider}'. Pick one of: {', '.join(_providers())}", file=sys.stderr)
        sys.exit(2)

    if action == 'unset':
        _set(provider, '')
        print(f"Removed the {provider} key")
        return

    # set
    key = ''
    if args.from_env:
        key = os.environ.get(args.from_env, '')
        if not key:
            print(f"{args.from_env} is empty or not set.", file=sys.stderr)
            sys.exit(1)
    elif args.stdin or not sys.stdin.isatty():
        key = sys.stdin.readline()
    else:
        import getpass

        key = getpass.getpass(f"{provider} API key: ")
    key = key.strip()
    if not key:
        print("No key given. Nothing was changed.", file=sys.stderr)
        sys.exit(1)
    try:
        _set(provider, key)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(f"Saved the {provider} key")


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser('keys', help='Show or save model provider API keys (Settings, Models and keys)')
    inner = parser.add_subparsers(dest='keys_action')
    listing = inner.add_parser('list', help='Show which providers have a key saved')
    listing.add_argument('--json', action='store_true', help='Print JSON')
    setting = inner.add_parser('set', help='Save a key (asked for, or read from stdin / a variable)')
    setting.add_argument('provider', help='Provider, e.g. anthropic, openai, google, openrouter')
    setting.add_argument('--stdin', action='store_true', help='Read the key from stdin')
    setting.add_argument('--from-env', default='', metavar='VAR', help='Read the key from this environment variable')
    clearing = inner.add_parser('unset', help='Remove a saved key')
    clearing.add_argument('provider')
    importing = inner.add_parser(
        'import-env', help='Save keys from the usual variables (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, ...)')
    importing.add_argument('--if-unset', action='store_true', help="Keep keys that are already saved")
    parser.set_defaults(func=manage_keys, provider='', stdin=False, from_env='', if_unset=False, json=False)
