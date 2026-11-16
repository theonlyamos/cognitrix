"""Removing a person from this server.

The account goes, and with it everything only they could see or use: chats,
memories, keys, saved sign-ins, linked chat apps, push devices, connected
apps, personal skills and settings. Their agents, tasks, teams, apps, pages
and knowledge either move to another person (the admin removing them, by
default) or are deleted too.

Kept on purpose: usage totals and the audit log, so the server's history
still adds up, and finished task runs.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any

logger = logging.getLogger('cognitrix.audit')

# (module, class, field). Moved to the new owner, or deleted when nobody
# takes them.
WORK = (
    ('cognitrix.models.agent', 'Agent', 'owner_id'),
    ('cognitrix.tasks.base', 'Task', 'owner_id'),
    ('cognitrix.tasks.automation', 'TaskTrigger', 'owner_id'),
    ('cognitrix.teams.base', 'Team', 'owner_id'),
    ('cognitrix.hosted_apps.models', 'HostedApp', 'owner_id'),
    ('cognitrix.artifact_pages.models', 'ArtifactPage', 'owner_id'),
    ('cognitrix.artifact_pages.models', 'ArtifactFile', 'owner_id'),
    ('cognitrix.artifacts', 'Artifact', 'user_id'),
    ('cognitrix.artifacts', 'DocumentArtifact', 'user_id'),
    ('cognitrix.knowledge.models', 'KnowledgeCollection', 'user_id'),
    ('cognitrix.knowledge.models', 'KnowledgeDocument', 'user_id'),
)

# Always deleted: private to the person, or secrets.
PRIVATE = (
    ('cognitrix.sessions.base', 'Session', 'user_id'),
    ('cognitrix.sessions.index', 'ChatIndex', 'user_id'),
    ('cognitrix.session_ownership', 'SessionOwnership', 'user_id'),
    ('cognitrix.models.memory', 'Memory', 'user_id'),
    ('cognitrix.models.memory', 'MemorySettings', 'user_id'),
    ('cognitrix.models.memory', 'MemoryChange', 'user_id'),
    ('cognitrix.models.memory', 'MemoryProfile', 'user_id'),
    ('cognitrix.memory.dream', 'DreamRun', 'user_id'),
    ('cognitrix.memory.dream', 'DreamProposal', 'user_id'),
    ('cognitrix.assistant.proactivity', 'ProactivitySettings', 'user_id'),
    ('cognitrix.assistant.proactivity', 'ProactivityDay', 'user_id'),
    ('cognitrix.assistant.brief', 'Brief', 'user_id'),
    ('cognitrix.assistant.followups', 'FollowUp', 'user_id'),
    ('cognitrix.assistant.mail', 'AssistantMail', 'user_id'),
    ('cognitrix.models.api_key', 'APIKey', 'user_id'),
    ('cognitrix.models.chat_channel', 'ChatChannel', 'user_id'),
    ('cognitrix.models.push_subscription', 'PushSubscription', 'user_id'),
    ('cognitrix.models.credential', 'Credential', 'owner_id'),
    ('cognitrix.models.mcp_server', 'MCPServer', 'owner_id'),
    ('cognitrix.models.computer_app', 'ComputerAppPermission', 'user_id'),
    ('cognitrix.models.voice_settings', 'VoiceSettings', 'user_id'),
    ('cognitrix.themes', 'ThemeSettings', 'user_id'),
    ('cognitrix.skills.personal', 'PersonalSkill', 'user_id'),
    ('cognitrix.skills.drafts', 'SkillDraft', 'user_id'),
    ('cognitrix.tasks.automation', 'AgentNotification', 'user_id'),
    ('cognitrix.tasks.standing', 'AgentOffer', 'user_id'),
    ('cognitrix.tasks.approvals', 'TaskApproval', 'user_id'),
    ('cognitrix.swarms.models', 'SwarmSettings', 'user_id'),
    ('cognitrix.swarms.models', 'SwarmWorker', 'user_id'),
    ('cognitrix.swarms.models', 'SwarmRun', 'user_id'),
    ('cognitrix.meetings.notes', 'MeetingNote', 'user_id'),
    ('cognitrix.artifact_pages.models', 'ArtifactConsent', 'user_id'),
    ('cognitrix.artifact_pages.models', 'ArtifactData', 'user_id'),
    ('cognitrix.usage.caps', 'SpendingCap', 'user_id'),
)

# Things shared with the person: their entry in shared_with is dropped.
SHARED = (
    ('cognitrix.models.agent', 'Agent'),
    ('cognitrix.hosted_apps.models', 'HostedApp'),
    ('cognitrix.artifact_pages.models', 'ArtifactPage'),
)


def _model(module: str, name: str) -> Any:
    return getattr(importlib.import_module(module), name)


async def _count(model: Any, field: str, key: str) -> int:
    try:
        return len(await model.find({field: key}) or [])
    except Exception:
        return 0


async def preview(key: str) -> dict[str, int]:
    """How much of their work there is, for the confirm dialog."""
    counts: dict[str, int] = {}
    for module, name, field in WORK[:5]:
        counts[name] = await _count(_model(module, name), field, key)
    from cognitrix.sessions.base import Session

    counts['chats'] = await _count(Session, 'user_id', key)
    return {
        'agents': counts['Agent'], 'tasks': counts['Task'], 'teams': counts['Team'],
        'apps': counts['HostedApp'], 'chats': counts['chats'],
    }


async def _stop_everything(key: str) -> None:
    try:
        from cognitrix.swarms import service as swarms

        await swarms.stop_all(key)
    except Exception:
        logger.exception('Removing a person: could not stop their swarms')
    try:
        from cognitrix.api.routes.tasks import _active_run, cancel_active_run
        from cognitrix.tasks.base import Task

        for task in await Task.find({'owner_id': key}) or []:
            run = await _active_run(task.id)
            if run is not None:
                await cancel_active_run(task, run)
    except Exception:
        logger.exception('Removing a person: could not cancel their task runs')
    try:
        from cognitrix.channels.service import get_service

        service = get_service()
        if service is not None:
            await service.cancel_user_turns(key)
    except Exception:
        logger.exception('Removing a person: could not stop their chat-app turns')
    try:
        from cognitrix.sessions import runs as chat_runs

        for run in list(chat_runs.active_runs(key)):
            chat_runs.stop_run(key, run.session_id)
    except Exception:
        logger.debug('Removing a person: could not stop their chat runs', exc_info=True)


async def _pause_automations(key: str) -> int:
    """Schedules, armed wake-ups and webhook/mail triggers carry the removed
    person's authority, which stops working once their account is gone. Pause
    them so the new owner turns them back on (which saves their own authority)."""
    from cognitrix.tasks.automation import WAKE_FIELDS, TaskTrigger
    from cognitrix.tasks.base import Task

    paused = 0
    for task in await Task.find({'owner_id': key}) or []:
        changes: dict[str, Any] = {}
        if task.schedule_enabled or task.schedule_requested_by:
            changes.update(schedule_enabled=False, schedule_requested_by=None,
                           schedule_authority_kind=None, schedule_authority_id=None)
        if task.wake_pending or task.wake_requested_by:
            changes.update({field: None for field in WAKE_FIELDS})
            changes['wake_pending'] = False
        if changes:
            await Task.update_one({'id': task.id}, changes)
            paused += 1
    for trigger in await TaskTrigger.find({'owner_id': key}) or []:
        await TaskTrigger.update_one({'id': trigger.id}, {
            'enabled': False, 'requested_by': None, 'authority_kind': None, 'authority_id': None})
        paused += 1
    return paused


async def remove(user: Any, *, new_owner: str | None) -> dict[str, Any]:
    """Delete ``user`` and their private data; move their work to
    ``new_owner`` (a principal key) or delete it when None."""
    from cognitrix.agents import access as agent_access
    from cognitrix.models import User

    key = agent_access.owner_key(user)
    await _stop_everything(key)
    moved: dict[str, int] = {}
    deleted: dict[str, int] = {}

    paused = 0
    if new_owner:
        try:
            paused = await _pause_automations(key)
        except Exception:
            logger.exception('Removing a person: could not pause their schedules and triggers')

    for module, name, field in WORK:
        try:
            model = _model(module, name)
            count = await _count(model, field, key)
            if not count:
                continue
            if new_owner:
                await model.update_many({field: key}, {field: new_owner})
                moved[name] = count
            else:
                await model.delete_many({field: key})
                deleted[name] = count
        except Exception:
            logger.exception('Removing a person: could not handle %s', name)

    for module, name, field in PRIVATE:
        try:
            model = _model(module, name)
            count = await _count(model, field, key)
            if count:
                await model.delete_many({field: key})
                deleted[name] = deleted.get(name, 0) + count
        except Exception:
            logger.exception('Removing a person: could not delete their %s rows', name)

    for module, name in SHARED:
        try:
            model = _model(module, name)
            for row in await model.all():
                shared = dict(getattr(row, 'shared_with', None) or {})
                if key in shared:
                    shared.pop(key, None)
                    await model.update_one({'id': row.id}, {'shared_with': shared})
        except Exception:
            logger.exception('Removing a person: could not unshare %s rows', name)

    await User.delete_many({'id': user.id})
    try:
        from cognitrix.usage import caps

        caps.forget(key)
    except Exception:
        pass
    return {'moved': moved, 'deleted': deleted, 'paused': paused}
