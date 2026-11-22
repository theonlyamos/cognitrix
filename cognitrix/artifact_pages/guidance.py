"""Artifacts as the normal way agents deliver work.

Every agent that has the artifact tools (they are added to every signed-in
chat and task turn, see ``Session._run_turn``) gets two things in its system
message:

* ``GUIDANCE``: when a result belongs in an artifact rather than the chat,
  and how to keep one artifact per deliverable (update, don't duplicate).
* ``context_block``: the artifacts this chat or task already made, with
  their ids and open comments, so the agent revises them instead of
  starting over.

It also holds the two places the app saves work as an artifact itself: a
task run's result (``save_task_result``) and a swarm's combined report
(``save_swarm_report``).
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger('cognitrix.log')

GUIDANCE = """## Artifacts
Artifacts are how you hand over substantial work. An artifact is a page the person opens beside the chat, keeps, shares and comments on; it keeps every version.
- Put a deliverable in an artifact (Publish Artifact), not in the chat: a report, research summary, document or draft, plan, spec, comparison, table of results, dashboard, checklist, tracker (format "sheet"), diagram or brainstorm (format "whiteboard"), or a small interactive tool. A good test: would the person want to come back to it, share it or ask for changes to it? Then it is an artifact.
- Keep the chat for the conversation: answers, questions, short explanations and small code snippets. After publishing, reply in one to three lines: what you made and the one thing worth knowing. Don't paste the artifact's contents into the chat as well.
- One artifact per deliverable. When the person asks for changes, or you have new information, update the same artifact (Publish Artifact with its artifact_id, and a short note on what changed) instead of making a new one. Read Artifact first when people may have edited it or left comments.
- Earlier artifacts are listed below when this chat or task has any. For one from another chat, find it with List Artifacts.
- When a comment on an artifact reaches you, make the change in the artifact and reply in the comment thread (Artifact Comments) saying what you changed.
- Files that belong in the person's folders or repository (code, configs, data files) still go there with the file tools. An artifact is for something to look at, read or use.
- When the result of a job is a file you produced (a video, image, chart, audio clip, PDF or Word document, slides, spreadsheet, zip or build), show the final files with Share File: the person sees them as preview cards they can play, open and download. Share only the finished files, then reply in a line or two."""

TASK_GUIDANCE = (
    '- In a task run, publish the run\'s report or output as an artifact with the same title every run, so it stays one '
    'link with a version per run. End with a short summary of what changed.'
)

MAX_LISTED = 12

# Task.result_artifact values; None means auto.
RESULT_ARTIFACT_MODES = (None, 'auto', 'always', 'off')

# A result long or structured enough to be worth keeping as a page.
MIN_RESULT_CHARS = 1200
_STRUCTURE = re.compile(r'^(#{1,4} |\|.*\|\s*$|[-*] .+)', re.MULTILINE)


def _plain(value: Any) -> str:
    return str(getattr(value, 'value', value) or '')


async def _open_threads(page_id: str) -> int:
    from cognitrix.artifact_pages.models import ArtifactComment

    rows = await ArtifactComment.find({'page_id': str(page_id), 'thread_id': ''}) or []
    return sum(1 for r in rows if not r.resolved)


async def related_pages(owner: str, *, session_id: str = '', task_id: str = '') -> list[Any]:
    """Artifacts this chat or task made (newest first)."""
    from cognitrix.artifact_pages.models import ArtifactPage

    found: dict[str, Any] = {}
    for field, value in (('task_id', task_id), ('session_id', session_id)):
        if not value:
            continue
        for row in await ArtifactPage.find({'owner_id': str(owner), field: str(value)}) or []:
            found[str(row.id)] = row
    if task_id:
        # Pages an earlier run of this task made but a chat has since taken over
        # still show up through their versions.
        from cognitrix.artifact_pages.models import ArtifactVersion

        for version in await ArtifactVersion.find({'task_id': str(task_id)}) or []:
            if version.page_id in found:
                continue
            page = await ArtifactPage.get(version.page_id)
            if page is not None and page.owner_id == str(owner):
                found[str(page.id)] = page
    return sorted(found.values(), key=lambda p: p.updated or p.created or '', reverse=True)


async def context_block(owner: str | None, *, session_id: str = '', task_id: str = '') -> str:
    """The Artifacts section of the system message for one turn."""
    parts = [GUIDANCE]
    if task_id:
        parts.append(TASK_GUIDANCE)
    if not owner:
        return '\n'.join(parts)
    try:
        pages = await related_pages(str(owner), session_id=str(session_id or ''), task_id=str(task_id or ''))
    except Exception:
        logger.debug('Could not list artifacts for the prompt', exc_info=True)
        pages = []
    if pages:
        where = 'this task' if task_id else 'this chat'
        lines = [f'\nArtifacts {where} already made (update these rather than making new ones):']
        for page in pages[:MAX_LISTED]:
            try:
                open_count = await _open_threads(str(page.id))
            except Exception:
                open_count = 0
            extra = f', {open_count} open comment{"s" if open_count != 1 else ""}' if open_count else ''
            lines.append(f'- "{page.title}" (artifact_id {page.id}, {page.format}, version {page.version}{extra})')
        if len(pages) > MAX_LISTED:
            lines.append(f'- and {len(pages) - MAX_LISTED} more (List Artifacts)')
        parts.append('\n'.join(lines))
    return '\n'.join(parts)


CLAIM_INDEX = 'ux_artifact_result_claims_run'


async def ensure_schema() -> None:
    """Create the unique index that makes per-run result claims atomic."""
    from odbms import DBMS

    from cognitrix.artifact_pages.models import ArtifactResultClaim

    database = DBMS.Database
    if getattr(database, 'dbms', '') not in ('sqlite', 'postgresql', 'mysql'):
        return
    table = ArtifactResultClaim.table_name()
    statement = (f'CREATE UNIQUE INDEX {CLAIM_INDEX} ON {table} (run_id)'
                 if database.dbms == 'mysql' else
                 f'CREATE UNIQUE INDEX IF NOT EXISTS {CLAIM_INDEX} ON {table} (run_id)')
    try:
        await database.query(statement)
    except Exception as exc:
        if 'exist' not in str(exc).lower() and 'duplicate' not in str(exc).lower():
            raise


async def _claim_run(task_id: str, run_id: str) -> bool:
    """True for exactly one caller per run."""
    from cognitrix.artifact_pages.models import ArtifactResultClaim

    if await ArtifactResultClaim.find({'run_id': run_id}):
        return False
    try:
        await ArtifactResultClaim(run_id=run_id, task_id=str(task_id)).save()
    except Exception as exc:
        if 'unique' in str(exc).lower() or 'duplicate' in str(exc).lower():
            return False
        raise
    return True


async def _unclaim_run(run_id: str) -> None:
    from cognitrix.artifact_pages.models import ArtifactResultClaim

    try:
        await ArtifactResultClaim.delete_many({'run_id': run_id})
    except Exception:
        logger.debug('Could not release the result claim of run %s', run_id, exc_info=True)


def worth_keeping(text: str) -> bool:
    """A result long or structured enough to be a page rather than a line."""
    body = str(text or '').strip()
    if len(body) >= MIN_RESULT_CHARS:
        return True
    return len(body) >= 400 and len(_STRUCTURE.findall(body)) >= 3


async def save_task_result(task: Any, run: Any, text: str) -> Any | None:
    """Keep a finished run's result as an artifact, one per task, a version
    per run. Skipped when the task turned it off, the result is short, or
    the run already published an artifact itself. Returns the page or None.
    """
    from cognitrix.artifact_pages import store
    from cognitrix.artifact_pages.models import ArtifactVersion

    mode = _plain(getattr(task, 'result_artifact', 'auto')) or 'auto'
    if mode == 'off':
        return None
    owner = str(getattr(task, 'owner_id', '') or getattr(run, 'requested_by', '') or '')
    if not owner or _plain(getattr(run, 'status', '')) != 'completed':
        return None
    if mode != 'always' and not worth_keeping(text):
        return None
    if not str(text or '').strip():
        return None
    run_id = str(getattr(run, 'id', '') or '')
    task_id = str(getattr(task, 'id', '') or '')
    if run_id and await ArtifactVersion.find({'run_id': run_id}):
        return None  # the run published its own page (or this ran already)
    # Completion and recovery can both reach a finished run: one claim per run.
    if run_id and not await _claim_run(task_id, run_id):
        return None
    title = str(getattr(task, 'title', '') or 'Task result')
    agent_id = str(getattr(run, 'agent_id', '') or getattr(task, 'assigned_agent_id', '') or '')
    if not agent_id:
        agents = getattr(task, 'assigned_agents', None) or []
        agent_id = str(agents[0]) if agents else ''
    stamp = str(getattr(run, 'completed_at', '') or getattr(run, 'finished_at', '') or store.now())[:16].replace('T', ' ')
    try:
        page, _created = await store.publish(
            user_key=owner, title=title, content=str(text), fmt='markdown',
            note=f'Result of the run on {stamp} UTC', agent_id=agent_id, task_id=task_id, run_id=run_id,
        )
    except store.PageError as exc:
        logger.info('Task %s result not saved as an artifact: %s', task_id, exc.message)
        return None
    except BaseException:
        if run_id:
            await _unclaim_run(run_id)  # let a later pass try again
        raise
    return page


MAX_WORKER_CHARS = 20_000


def _worker_result(worker: Any) -> str:
    path = str(getattr(worker, 'result_path', '') or '')
    if not path:
        return ''
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            text = handle.read(MAX_WORKER_CHARS + 1)
    except OSError:
        return ''
    if len(text) > MAX_WORKER_CHARS:
        text = text[:MAX_WORKER_CHARS] + '\n\n_(cut short)_'
    return text


def _swarm_markdown(swarm: Any, workers: list[Any]) -> str:
    lines = [f'# {swarm.title}', '']
    for worker in sorted(workers, key=lambda w: getattr(w, 'position', 0) or 0):
        role = f' ({worker.role})' if getattr(worker, 'role', '') else ''
        status = _plain(getattr(worker, 'status', ''))
        lines.append(f'## {worker.name}{role}')
        if status != 'done':
            lines += [f'_{status or "not finished"}_: {getattr(worker, "error", "") or "no result"}', '']
            continue
        lines += [(_worker_result(worker) or str(getattr(worker, 'summary', '') or '(no summary)')).strip(), '']
    judge = getattr(swarm, 'judge', None) or {}
    if _plain(getattr(swarm, 'mode', '')) == 'best_of' and judge:
        lines += ['## Judge', f'Best attempt: {judge.get("winner") or "(none)"}', '', str(judge.get('verdict') or ''), '']
    return '\n'.join(lines).strip() + '\n'


async def save_swarm_report(swarm: Any, workers: list[Any]) -> Any | None:
    """Keep a finished swarm's results as one artifact in the lead's chat."""
    from cognitrix.artifact_pages import store

    owner = str(getattr(swarm, 'user_id', '') or '')
    if not owner or not any(_plain(getattr(w, 'status', '')) == 'done' for w in workers):
        return None
    try:
        page, _created = await store.publish(
            user_key=owner, title=f'{swarm.title} (swarm results)'[:store.MAX_TITLE],
            content=_swarm_markdown(swarm, workers), fmt='markdown', note='Workers\' results',
            agent_id=str(getattr(swarm, 'agent_id', '') or ''), session_id=str(getattr(swarm, 'session_id', '') or ''),
            task_id=str(getattr(swarm, 'task_id', '') or ''), run_id=str(getattr(swarm, 'run_id', '') or ''),
        )
    except store.PageError as exc:
        logger.info('Swarm %s results not saved as an artifact: %s', getattr(swarm, 'id', ''), exc.message)
        return None
    return page
