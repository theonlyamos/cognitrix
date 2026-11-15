"""Cognitrix as an Agent Client Protocol server (``cognitrix acp``).

Editors that speak ACP (Zed, JetBrains IDEs, Neovim plugins and others) start
``cognitrix acp`` as a subprocess and chat with your Cognitrix agents over
stdin/stdout. See ``server.py``.
"""


async def command(args):  # noqa: ANN001
    """``cognitrix acp`` (imports the protocol package only when used)."""
    from cognitrix.acp.server import command as run

    await run(args)
