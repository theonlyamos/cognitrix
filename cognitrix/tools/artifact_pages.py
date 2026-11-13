"""Artifact tools: agents publish a web page (HTML or Markdown) the person
opens beside the chat, shares and comments on; read one back; and answer
comments on it. See ``cognitrix/artifact_pages``.

Added per turn to chats and task runs that belong to a signed-in person (not
stored on the agent); see ``Session._run_turn`` and
``tasks.orchestrator._add_automation_tools``.
"""
from __future__ import annotations

from cognitrix.artifact_pages import comments as page_comments
from cognitrix.artifact_pages import store
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import EntityRef, ToolOutcome, current_execution_context

ARTIFACT_TOOL_CATEGORY = 'artifacts'
ARTIFACT_TOOL_NAMES = ('publish_artifact', 'read_artifact', 'artifact_comments')
READ_LIMIT = 60_000


def _owner() -> str | None:
    ctx = current_execution_context()
    if ctx.restricted or not ctx.user_id:
        return None
    return str(ctx.user_id)


def _agent_id(parent) -> str:
    ctx = current_execution_context()
    return str(ctx.agent_id or getattr(parent, 'id', '') or '')


def _failure(exc: store.PageError) -> ToolOutcome:
    return ToolOutcome.failure(exc.code, exc.message)


@tool(category=ARTIFACT_TOOL_CATEGORY, retryable=False, max_attempts=1,
      supported_interfaces=['web', 'ws', 'task'])
async def publish_artifact(title: str, content: str, format: str = 'html', artifact_id: str = '',
                           note: str = '', powers: list[str] | None = None, *, parent=None) -> ToolOutcome:
    """Publish a web page the person opens beside the chat, can share with a link, and comment on.
    Use it when your answer is easier to look at or use than to read: a report, comparison, dashboard,
    chart, plan, checklist, interactive tool or a document someone else will read.

    The page is ONE self-contained file. format "html": a full page with inline <style> and <script>;
    scripts may also come from cdnjs.cloudflare.com, cdn.jsdelivr.net, unpkg.com, cdn.tailwindcss.com
    or code.jquery.com (pin exact versions), fonts from Google Fonts; images must be inline (data: URIs or SVG).
    The page cannot fetch other websites. Make it readable in light and dark (prefers-color-scheme) and
    at phone width. format "markdown": a document rendered with clean styling.

    To change a page, publish again with its artifact_id: that adds a new version at the same link and
    anyone looking at it sees the update. Publishing again with the same title from the same chat or
    task also updates it. The person sees a card in the chat that opens the page.

    Args:
        title (str): Short name, 2-6 words.
        content (str): The whole page source (HTML or Markdown), at most 5 MB.
        format (str): "html" (default) or "markdown".
        artifact_id (str): Id of an artifact to update; empty for a new one.
        note (str): One line on what changed in this version.
        powers (list[str] | None): Optional things the page may do, each viewer is asked first:
            "ask" (window.cognitrix.ask(prompt) -> text from an agent), "data" (window.cognitrix.data.get/set/list/remove(key, value, {shared:true})
            saves small JSON values), "download" (window.cognitrix.download(filename, text, mimeType)),
            "apps" (window.cognitrix.apps.list() and window.cognitrix.apps.call(app, tool, args) read the viewer's connected apps).
            All return Promises. Leave empty for a plain page.
    """
    user_key = _owner()
    if not user_key:
        return ToolOutcome.failure('no_owner', 'Artifacts need a signed-in person to belong to.')
    ctx = current_execution_context()
    try:
        page, created = await store.publish(
            user_key=user_key, title=title, content=content, fmt=format, page_id=str(artifact_id or '').strip(),
            note=note, powers=powers, agent_id=_agent_id(parent), session_id=str(ctx.session_id or ''),
            task_id=str(ctx.task_id or ''), run_id=str(ctx.run_id or ''),
        )
    except store.PageError as exc:
        return _failure(exc)
    what = 'Published' if created else f'Updated to version {page.version}:'
    return ToolOutcome.success(
        f'{what} "{page.title}" (artifact_id {page.id}, version {page.version}). '
        f'The person can open it from the card in the chat or at /artifacts/{page.id}.',
        entities=[EntityRef(type='artifact_page', id=str(page.id), name=page.title)],
    )


@tool(category=ARTIFACT_TOOL_CATEGORY, retryable=False, max_attempts=1,
      supported_interfaces=['web', 'ws', 'task'])
async def read_artifact(artifact_id: str, version: int = 0, *, parent=None) -> ToolOutcome:
    """Read an artifact's source and its open comments, before changing it or answering about it.

    Args:
        artifact_id (str): The artifact id.
        version (int): A version number; 0 for the newest.
    """
    user_key = _owner()
    if not user_key:
        return ToolOutcome.failure('no_owner', 'Reading artifacts needs a signed-in person.')
    try:
        page, role = await store.page_for(str(artifact_id or '').strip(), user_key)
        source = store.read_source(page, int(version or 0) or None)
    except store.PageError as exc:
        return _failure(exc)
    cut = len(source) > READ_LIMIT
    rows = await page_comments.thread_rows(str(page.id))
    open_threads = [t for t in page_comments.threads(rows) if not t['resolved']]
    lines = [f'"{page.title}" (artifact_id {page.id}, {page.format}, version {int(version or 0) or page.version} '
             f'of {page.version}, your access: {role}).']
    if open_threads:
        lines.append('Open comments:')
        for thread in open_threads[:20]:
            where = f' on "{thread["anchor"].get("text")}"' if thread['anchor'].get('text') else ''
            lines.append(f'- thread {thread["id"]}{where}: {thread["author_name"]}: {thread["body"][:300]}')
            for reply in thread['replies'][-3:]:
                lines.append(f'    {reply["author_name"]}: {reply["body"][:300]}')
    lines.append('Source:' + (' (cut short)' if cut else ''))
    lines.append(source[:READ_LIMIT])
    return ToolOutcome.success('\n'.join(lines))


@tool(category=ARTIFACT_TOOL_CATEGORY, retryable=False, max_attempts=1,
      supported_interfaces=['web', 'ws', 'task'])
async def artifact_comments(artifact_id: str, action: str = 'list', thread_id: str = '', text: str = '',
                            *, parent=None) -> ToolOutcome:
    """See and answer comments people left on an artifact.

    action:
    - "list": every comment thread (open first).
    - "reply": answer in a thread (thread_id, text). Say what you changed, or answer the question.
    - "resolve": mark a thread done (thread_id). Only when the person asks you to close it; after a change, reply and leave it open so they can check.

    Args:
        artifact_id (str): The artifact id.
        action (str): "list", "reply" or "resolve".
        thread_id (str): The thread, for reply and resolve.
        text (str): Your reply, for reply.
    """
    user_key = _owner()
    if not user_key:
        return ToolOutcome.failure('no_owner', 'Comments need a signed-in person.')
    act = str(action or 'list').strip().lower()
    try:
        page, role = await store.page_for(str(artifact_id or '').strip(), user_key)
        if act == 'list':
            rows = await page_comments.thread_rows(str(page.id))
            items = sorted(page_comments.threads(rows), key=lambda t: t['resolved'])
            if not items:
                return ToolOutcome.success('No comments yet.')
            lines = []
            for t in items:
                state = 'resolved' if t['resolved'] else 'open'
                lines.append(f'- thread {t["id"]} ({state}) {t["author_name"]}: {t["body"][:400]}')
                for r in t['replies']:
                    lines.append(f'    {r["author_name"]}: {r["body"][:400]}')
            return ToolOutcome.success('\n'.join(lines))
        if not store.can_edit(role):
            return ToolOutcome.failure('not_allowed', 'You can only read comments on this artifact.')
        if act == 'reply':
            agent_name = str(getattr(parent, 'name', '') or 'Agent')
            await page_comments.add(page, author_id=f'{page_comments.AGENT_PREFIX}{_agent_id(parent)}',
                                    author_name=agent_name, body=text, thread_id=str(thread_id or ''))
            from cognitrix.artifact_pages.models import ArtifactComment

            reply = (await ArtifactComment.find({'thread_id': str(thread_id)}) or [])
            newest = max(reply, key=lambda r: r.created) if reply else None
            if newest is not None:
                people, _ = await store.names()
                await page_comments.notify(page, newest, people=people)
            return ToolOutcome.success('Replied in the thread.')
        if act == 'resolve':
            await page_comments.set_resolved(page, str(thread_id or ''), True)
            return ToolOutcome.success('Thread resolved.')
    except store.PageError as exc:
        return _failure(exc)
    return ToolOutcome.failure('bad_action', 'action is "list", "reply" or "resolve"')
