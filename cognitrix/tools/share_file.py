"""Share File: show files you made to the person, right in the chat.

Rides along with the artifact tools (same category), so every signed-in chat
and task turn has it. See ``cognitrix/produced_files``.
"""
from __future__ import annotations

from cognitrix.tools.artifact_pages import ARTIFACT_TOOL_CATEGORY
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome


def _size(n: int) -> str:
    if n >= 1024 * 1024:
        return f'{n / (1024 * 1024):.1f} MB'
    return f'{max(1, n // 1024)} KB'


@tool(category=ARTIFACT_TOOL_CATEGORY, retryable=False, max_attempts=1,
      supported_interfaces=['web', 'ws', 'task'])
async def share_file(paths: list[str], caption: str = '') -> ToolOutcome:
    """Show files you made to the person, as preview cards in the chat: videos and audio play in place,
    images, PDFs, slides, documents and spreadsheets show a preview, archives list what's inside, and every
    card can be opened full size and downloaded.

    Use it at the end of any job whose result is a file (a video you rendered, an image, a chart, a PDF or
    Word report, a spreadsheet, a zip, an audio clip, a build), with the final files only, not drafts or
    intermediate files. Works wherever you worked: this machine's folders, the sandbox, or the person's PC.
    After sharing, reply in a line or two; don't repeat the paths. Use Publish Artifact instead for a page
    you write for them to read; use this for files you produced.

    Args:
        paths (list[str]): Paths of the files to show (up to 10), absolute or relative to where you work.
        caption (str): Optional one line about the files.
    """
    from cognitrix.produced_files import service

    rows, errors = await service.share(list(paths or []), caption)
    if not rows:
        return ToolOutcome.failure('not_shared', 'Nothing was shared. ' + ' '.join(errors or ['Give at least one path.']))
    lines = [f'Shared {len(rows)} file{"s" if len(rows) != 1 else ""} with the person; they see '
             f'{"them" if len(rows) != 1 else "it"} as preview cards in the chat:']
    lines += [f'- {r.name} ({r.kind}, {_size(int(r.size_bytes or 0))})' for r in rows]
    lines += [f'Not shared: {e}' for e in errors]
    return ToolOutcome.success('\n'.join(lines), files=[service.ref(r) for r in rows],
                               warnings=errors)
