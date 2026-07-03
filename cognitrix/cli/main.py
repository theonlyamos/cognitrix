"""
Main CLI entry point and orchestration.
"""
import asyncio
import logging
import os
import sys

from .args import get_arguments


def main():
    """Main entry point for the Cognitrix CLI."""
    # Windows consoles often default to cp1252; model output and status
    # messages contain non-latin characters, which would otherwise raise
    # UnicodeEncodeError mid-turn and abort tool execution.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    try:
        args = get_arguments()

        # --dangerously-skip-permissions: fully unattended, no safety gate and
        # no command whitelist. Wired through the same env vars the approval
        # gate and bash tool read, so it takes effect before any tool runs.
        # Sandbox/container use only.
        if getattr(args, 'dangerously_skip_permissions', False):
            os.environ['COGNITRIX_AUTO_APPROVE'] = '1'
            os.environ['COGNITRIX_SANDBOX_SHELL'] = '1'
            logging.warning(
                "--dangerously-skip-permissions: auto-approving all tool calls and "
                "running the bash tool without the command whitelist."
            )

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
