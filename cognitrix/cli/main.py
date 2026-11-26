"""
Main CLI entry point and orchestration.
"""
import asyncio
import logging
import sys

from cognitrix.common.encoding import ensure_utf8_stdio

from .args import get_arguments

WARNING = """\
!! --dangerously-skip-permissions: nothing will ask for approval in this run.
!! Every tool call, shell command, coding agent, file change and send runs at
!! once, in this session, its sub-agents and any task runs it starts. Your
!! saved settings are not changed. Only use it somewhere you can throw away
!! (a container, a VM, a benchmark sandbox).
"""


def _skip_permissions(args) -> None:
    """--dangerously-skip-permissions / --yolo: no approval prompts at all.

    Every card is answered Yes for this process only (the account's
    "Auto-approve everything" machinery, safety/auto_approve_all.py), the
    bash tool runs any command in a real shell, and task runs this run
    queues for a worker carry the same answer. Never for the web server:
    other people's chats would get it too.
    """
    if str(getattr(args, 'ui', '') or '').lower() == 'web' and not (
            getattr(args, 'prompt', '') or getattr(args, 'prompt_file', '')):
        print('--dangerously-skip-permissions is ignored with --ui web: it would answer '
              "approvals for everyone's chats on the server.", file=sys.stderr)
        return
    from cognitrix import app_settings
    from cognitrix.safety import auto_approve_all

    auto_approve_all.enable_for_this_run()
    app_settings.override('COGNITRIX_SANDBOX_SHELL', '1')
    # On stderr, so the answer of a one-shot `-p` run stays clean on stdout.
    print(WARNING, file=sys.stderr, end='', flush=True)


def main():
    """Main entry point for the Cognitrix CLI."""
    # Windows consoles often default to cp1252; model output and status
    # messages contain non-latin characters, which would otherwise raise
    # UnicodeEncodeError mid-turn and abort tool execution.
    ensure_utf8_stdio()
    try:
        args = get_arguments()

        if getattr(args, 'dangerously_skip_permissions', False):
            _skip_permissions(args)

        # Check if the function is async and run appropriately
        if asyncio.iscoroutinefunction(args.func):
            asyncio.run(args.func(args))
        else:
            args.func(args)

    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(1)
    except Exception as e:
        logging.exception(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
