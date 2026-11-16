"""Pull requests agents are watching (``cognitrix/tasks/pr_watch.py``).

``github_watches_api`` lists a person's watches and stops one.
``github_hooks_api`` is the public receiver for the repository webhooks that
watches add on a server GitHub can reach; every delivery must carry a valid
``X-Hub-Signature-256``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from cognitrix.api.routes.tasks import _user_key
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.tasks import pr_watch

logger = logging.getLogger('cognitrix.log')

github_watches_api = APIRouter(dependencies=[Depends(jwt_only)])
github_hooks_api = APIRouter()


@github_watches_api.get('/github/watches')
async def list_watches(ctx: AuthContext = Depends(get_auth_context)):
    rows = await pr_watch.watches_for(str(_user_key(ctx)))
    return [await pr_watch.describe(row) for row in rows]


async def _mine(watch_id: str, ctx: AuthContext) -> pr_watch.PullRequestWatch:
    watch = await pr_watch.PullRequestWatch.get(watch_id)
    if watch is None or str(watch.owner_id) != str(_user_key(ctx)):
        raise HTTPException(status_code=404, detail='Watch not found')
    return watch


@github_watches_api.post('/github/watches/{watch_id}/stop')
async def stop_watch(watch_id: str, ctx: AuthContext = Depends(get_auth_context)):
    watch = await _mine(watch_id, ctx)
    if watch.status == pr_watch.WATCHING:
        await pr_watch.stop(watch, 'Stopped by you.')
    return await pr_watch.describe(watch)


@github_watches_api.delete('/github/watches/{watch_id}')
async def delete_watch(watch_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """Forget a watch (stopping it first). Its task trigger goes too."""
    watch = await _mine(watch_id, ctx)
    if watch.status == pr_watch.WATCHING:
        await pr_watch.stop(watch, 'Stopped by you.')
    if watch.trigger_id:
        from cognitrix.tasks.automation import TaskTrigger

        await TaskTrigger.delete_many({'id': watch.trigger_id})
    await pr_watch.PullRequestWatch.delete_many({'id': watch.id})
    return {'deleted': True}


async def _check_soon() -> None:
    try:
        await pr_watch.poll_pr_watches(only_due=True)
    except Exception:
        logger.exception('Immediate pull request check failed; the scheduler will retry')


@github_hooks_api.post('/github/hooks/{hook_id}', status_code=202, include_in_schema=False)
async def receive_github_hook(hook_id: str, request: Request, background_tasks: BackgroundTasks):
    """A delivery from a repository webhook. It only says which pull request
    to look at: the check reads the pull request from GitHub itself."""
    declared = request.headers.get('content-length')
    if declared and declared.isdigit() and int(declared) > pr_watch.WEBHOOK_MAX_BYTES:
        raise HTTPException(status_code=413, detail='Payload too large')
    body = b''
    async for chunk in request.stream():
        body += chunk
        if len(body) > pr_watch.WEBHOOK_MAX_BYTES:
            raise HTTPException(status_code=413, detail='Payload too large')
    event = (request.headers.get('x-github-event') or '').strip()
    try:
        marked = await pr_watch.receive(hook_id, event, body, request.headers.get('x-hub-signature-256'))
    except LookupError:
        raise HTTPException(status_code=404, detail='Not Found')
    except PermissionError:
        raise HTTPException(status_code=401, detail='Bad signature')
    if marked:
        background_tasks.add_task(_check_soon)
    return {'event': event, 'matched': marked}
