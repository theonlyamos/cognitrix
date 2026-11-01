"""Team group chats: the signed-in user talks with all of a team's agents.

A message starts a background run (like a web chat turn, see
``cognitrix.sessions.runs``), so the browser follows it with the existing
``GET /agents/runs/{session_id}/events`` stream and can leave and come back.
Approvals and questions from the agents use the usual ``/agents/approval``
and ``/agents/question`` endpoints.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents import Agent
from cognitrix.agents.access import can_view
from cognitrix.common.security import get_current_user, jwt_only
from cognitrix.session_ownership import principal_key
from cognitrix.sessions import runs as chat_runs
from cognitrix.sessions.base import Session
from cognitrix.teams import group_chat
from cognitrix.teams.base import Team

logger = logging.getLogger('cognitrix.log')

team_chat_api = APIRouter(prefix='/teams', dependencies=[Depends(jwt_only)])

MAX_MESSAGE_CHARS = 32_000

# Strong refs so running group turns are not garbage-collected.
_TASKS: set[asyncio.Task] = set()


def _owner(user) -> str:
    return principal_key(user)


def _run_key(user) -> str:
    # Same key the /agents run, approval and question endpoints use.
    return str(getattr(user, 'id', None) or getattr(user, 'email', 'anon'))


def _run_agent_id(team_id: str) -> str:
    return f'team:{team_id}'


async def _team_and_members(team_id: str, user) -> tuple[Team, list[Agent]]:
    owner = _owner(user)
    team = await Team.get(team_id)
    if team is None or not can_view(team, owner):
        raise HTTPException(status_code=404, detail='Team not found')
    members: list[Agent] = []
    for agent_id in team.assigned_agents or []:
        agent = await Agent.find_one({'id': agent_id})
        if agent is None:
            continue
        if not can_view(agent, owner):
            raise HTTPException(status_code=403, detail='Not allowed to use this agent')
        members.append(agent)
    return team, members


async def _owned_chat(team_id: str, session_id: str, user) -> Session:
    session = await Session.get(session_id)
    if (
        session is None
        or not session.group_chat
        or str(session.team_id or '') != str(team_id)
        or str(session.user_id or '') != _owner(user)
    ):
        raise HTTPException(status_code=404, detail='Chat not found')
    return session


def _member_json(agent: Agent, team: Team) -> dict[str, Any]:
    return {
        'id': agent.id,
        'name': agent.name,
        'handle': group_chat.handle(agent.name),
        'lead': str(agent.id) == str(team.leader_id or ''),
        'provider': getattr(agent.llm, 'provider', None),
        'model': getattr(agent.llm, 'model', None),
    }


def _chat_summary(session: Session, run_key: str) -> dict[str, Any]:
    run = chat_runs.get_run(run_key, str(session.id))
    last = next(
        (e.get('timestamp') for e in reversed(session.chat or []) if e.get('timestamp')),
        None,
    )
    return {
        'id': session.id,
        'title': group_chat.chat_title(session.chat or []),
        'updated_at': last,
        'running': bool(run and run.running),
    }


@team_chat_api.get('/{team_id}/chats')
async def list_team_chats(team_id: str, user=Depends(get_current_user)):
    team, members = await _team_and_members(team_id, user)
    rows = await Session.find({'team_id': str(team.id), 'user_id': _owner(user)})
    chats = [_chat_summary(s, _run_key(user)) for s in rows if s.group_chat]
    chats.sort(key=lambda c: c['updated_at'] or '', reverse=True)
    return {
        'team': {'id': team.id, 'name': team.name, 'leader_id': team.leader_id},
        'members': [_member_json(m, team) for m in members],
        'chats': chats,
    }


@team_chat_api.get('/{team_id}/chats/{session_id}')
async def get_team_chat(team_id: str, session_id: str, user=Depends(get_current_user)):
    await _team_and_members(team_id, user)
    session = await _owned_chat(team_id, session_id, user)
    run = chat_runs.get_run(_run_key(user), str(session.id))
    return {
        **_chat_summary(session, _run_key(user)),
        'messages': session.chat or [],
        # While the team is still answering, the page shows the saved history
        # up to this run and replays the run on top of it.
        'run': run.summary() if run is not None and run.running else None,
    }


@team_chat_api.delete('/{team_id}/chats/{session_id}')
async def delete_team_chat(team_id: str, session_id: str, user=Depends(get_current_user)):
    session = await _owned_chat(team_id, session_id, user)
    run = chat_runs.get_run(_run_key(user), str(session.id))
    if run is not None and run.running:
        raise HTTPException(status_code=409, detail='Stop the chat before deleting it')
    await Session.delete_many({'id': str(session.id)})
    return {'status': 'deleted'}


@team_chat_api.post('/{team_id}/chats/{session_id}/stop')
async def stop_team_chat(team_id: str, session_id: str, user=Depends(get_current_user)):
    await _owned_chat(team_id, session_id, user)
    if not chat_runs.stop_run(_run_key(user), session_id):
        raise HTTPException(status_code=409, detail='Nothing is running in this chat')
    return {'status': 'stopping'}


class TeamChatMessage(BaseModel):
    message: str
    session_id: str | None = None
    reasoning_effort: str | None = None


@team_chat_api.post('/{team_id}/chats', status_code=202)
async def send_team_chat(team_id: str, body: TeamChatMessage, user=Depends(get_current_user)):
    message = (body.message or '').strip()
    if not message:
        raise HTTPException(status_code=400, detail='message is required')
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=400, detail='message is too long')
    team, members = await _team_and_members(team_id, user)
    if not members:
        raise HTTPException(status_code=400, detail='This team has no agents yet')

    run_key = _run_key(user)
    if body.session_id:
        session = await _owned_chat(team_id, body.session_id, user)
        running = chat_runs.get_run(run_key, str(session.id))
        if running is not None and running.running:
            raise HTTPException(
                status_code=409,
                detail='The team is still answering the last message',
            )
    else:
        session = Session(team_id=str(team.id), user_id=_owner(user), group_chat=True)

    history_len = len(session.chat or [])
    session.update_history(group_chat.transcript_entry(message))
    await session.save()

    from cognitrix.providers.base import normalize_reasoning_effort

    task: asyncio.Task | None = None

    def stop() -> bool:
        if task is None or task.done():
            return False
        task.cancel()
        return True

    run = chat_runs.start_run(
        run_key,
        _run_agent_id(str(team.id)),
        str(session.id),
        prompt=message,
        history_len=history_len,
        stop=stop,
    )
    task = asyncio.create_task(_run_turn(
        session, team, members, message,
        run=run,
        run_key=run_key,
        effort=normalize_reasoning_effort(body.reasoning_effort) or None,
    ))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {'session_id': session.id, 'run_id': run.run_id, 'status': 'running'}


async def _run_turn(
    session: Session,
    team: Team,
    members: list[Agent],
    message: str,
    *,
    run: chat_runs.ChatRun,
    run_key: str,
    effort: str | None,
) -> None:
    from cognitrix.providers.base import turn_reasoning_effort
    from cognitrix.questions.broker import QuestionTurnContext, question_turn_ctx
    from cognitrix.safety.approval_gate import web_turn_ctx
    from cognitrix.tools.utils import ToolExecutionContext, signed_in_host_process_mode

    session_id = str(session.id)

    async def emit(payload: dict[str, Any]) -> None:
        if isinstance(payload, dict):
            run.record({**payload, 'session_id': session_id})

    async def run_agent(agent: Agent, scratch: Session, trigger: str, out) -> None:
        await scratch(
            trigger,
            agent,
            interface='web',
            stream=True,
            output=out,
            wsquery={'type': 'generate', 'action': 'chat_message'},
            record_history=True,
            persist_history=False,
            compact_history=False,
            tool_context=ToolExecutionContext(
                session_id=session_id,
                user_id=run_key,
                agent_id=str(agent.id),
                host_process_mode=signed_in_host_process_mode(),
            ),
        )

    terminal: dict[str, Any] = {'type': 'turn_complete', 'content': '', 'session_id': session_id}
    approval_token = web_turn_ctx.set({
        'emit': emit,
        'session_id': session_id,
        'bypass': False,
        'user_key': run_key,
    })
    question_token = question_turn_ctx.set(QuestionTurnContext(
        emit=emit,
        session_id=session_id,
        stream_id='group',
        user_key=run_key,
    ))
    effort_token = turn_reasoning_effort.set(effort)
    try:
        await group_chat.run_group_turn(
            session, team, members, message, emit=emit, run_agent=run_agent,
        )
    except asyncio.CancelledError:
        terminal = {'type': 'turn_stopped', 'content': '', 'session_id': session_id}
    except Exception as exc:
        logger.exception('Group chat turn failed')
        run.record({'type': 'error', 'content': str(exc), 'session_id': session_id})
        # The error event above already told the page; end the run normally.
    finally:
        turn_reasoning_effort.reset(effort_token)
        question_turn_ctx.reset(question_token)
        web_turn_ctx.reset(approval_token)
        run.finish(terminal)
