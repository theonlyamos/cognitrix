"""Human-in-the-loop approval system for risky operations."""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from cognitrix.app_settings import get as app_setting
from cognitrix.common.encoding import status_print
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

logger = logging.getLogger('cognitrix.log')


# Tools a person must answer even with a turn's bypass flag on: giving an
# agent a new folder or write access widens what it can touch. The person's
# own "Auto-approve everything" setting does answer them (auto_approve_all.py).
_NEVER_AUTO_APPROVED = frozenset({'request_folder', 'copy_back_from_sandbox', 'request_access'})

# Risk categories only a person answers, every time: no remembered answer,
# bypass or reviewer stands in. Only the person's own "Auto-approve
# everything" setting answers them for the person (never person_takeover). Taking over the person's
# screen, the person taking over the agent's computer, and an agent asking
# for a tool or connected app it doesn't have (Request Access).
ALWAYS_ASK_CATEGORIES = frozenset({'screen_takeover', 'person_takeover', 'access_request'})

# How long a request to take over the agent's computer waits for Take over or
# Skip (other approvals wait the gate's usual timeout).
TAKEOVER_ANSWER_TIMEOUT = 30 * 60.0


def _takeover_target(tool_call, scope: str | None) -> dict:
    """What a Take over card needs: the agent's desktop and the reason."""
    desktop = 'main'
    try:
        from cognitrix.computer.sandbox import desktop_name, note_takeover_asked
        from cognitrix.projects.context import sandbox_identity

        desktop = desktop_name(sandbox_identity(scope))
        note_takeover_asked(desktop)
    except Exception:
        logger.debug('No desktop for a take-over request', exc_info=True)
    return {'desktop': desktop, 'reason': str((tool_call.params or {}).get('reason') or '')[:200]}


def _normal_name(name: str) -> str:
    return str(name or '').strip().lower().replace(' ', '_')


def _auto_approve_enabled() -> bool:
    return app_setting('COGNITRIX_AUTO_APPROVE', '').strip().lower() in ('1', 'true', 'yes')


# Per-turn web context: lets check_approval reach the SSE emit callback and the
# turn's owner without threading them through every call layer. Set by the SSE
# runner for a web chat turn; None for CLI/task/programmatic paths.
web_turn_ctx: ContextVar[dict | None] = ContextVar('web_turn_ctx', default=None)

# Pending browser approvals keyed by request_id. Module-level (not per gate
# instance) so the REST resolve endpoint completes a future created by whichever
# gate the turn used.
# ponytail: in-process, single-worker assumption — a multi-instance deploy needs
# shared state (redis) for the pending map.
_WEB_PENDING: dict[str, dict] = {}

# Per-turn approver for an editor connected over the Agent Client Protocol
# (cognitrix/acp): asks the editor, which shows its own permission prompt.
# Called with (tool_call, risk, person_only); returns an ApprovalResult.
acp_approver: ContextVar = ContextVar('acp_approver', default=None)
_web_request_counter = 0


def resolve_web_approval(request_id: str, approved: bool, user_key: str | None = None,
                         remember: bool = False, remember_shell: bool = False,
                         remember_app: str | None = None, always: bool = False,
                         grant: str | None = None) -> bool:
    """Resolve a pending browser approval. Returns False if unknown or not the owner.

    `remember_app` (only for a background action in an app window) is 'chat'
    to stop asking about that app in this chat, or 'always' to save it as
    the person's standing answer.

    `remember_shell` (only for a full-shell command) lets every full-shell
    command in that chat run without asking again until the app restarts.

    `always` saves the card's "Always allow" offer (``entry['always']``): the
    tool for that agent, or the command's prefix on that computer.

    `grant` (only for Request Access) is 'chat' (allow in this chat) or
    'agent' (add it to the agent; only offered to its owner). Approving
    without one, from a notification or a chat app, allows it in the chat.

    `user_key` scopes the answer to the user who owns the turn — a different
    authenticated user cannot approve someone else's pending tool call.
    """
    entry = _WEB_PENDING.get(request_id)
    if not entry:
        return False
    owner = entry.get('user_key')
    if user_key is not None and owner is not None and owner != user_key:
        return False
    future = entry.get('future')
    if future is not None and not future.done():
        future.set_result(ApprovalResult(
            approved=approved, remember=remember or remember_shell,
            remember_shell=bool(approved and remember_shell and entry.get('full_shell')),
            remember_app=(remember_app if approved and entry.get('app')
                          and remember_app in ('chat', 'always') else None),
            always=bool(approved and always and entry.get('always')),
            grant=_grant_answer(entry, approved, grant),
        ))
    return True


def _grant_answer(entry: dict, approved: bool, grant: str | None) -> str | None:
    access = entry.get('access')
    if not approved or not isinstance(access, dict):
        return None
    if grant == 'agent' and access.get('can_add'):
        return 'agent'
    return 'chat'

def _app_label(app: str) -> str:
    from cognitrix.computer.apps import app_label

    return app_label(app)


# Prefix of the tool-result message for a denied operation. The session loop's
# deny-loop breaker matches on it — keep producer and matcher in sync.
OPERATION_BLOCKED_PREFIX = "Operation blocked"


class ApprovalStatus(Enum):
    """Approval request status."""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"


@dataclass
class ToolCall:
    """Represents a tool call request."""
    tool_name: str
    params: dict
    # Set for a background action in an app window: the app's executable
    # name, so the person can answer for the app instead of the single call.
    app: str | None = None
    # Request Access: what the agent asks for, as the card shows it
    # (cognitrix/tools/access.py card_for).
    access: dict | None = None

    def dict(self):
        return {'tool_name': self.tool_name, 'params': self.params}


@dataclass
class ApprovalResult:
    """Result of an approval request."""
    approved: bool
    remember: bool = False  # Remember for this session
    permanent: bool = False  # Remember permanently
    remember_shell: bool = False  # Allow every full-shell command in this chat
    remember_app: str | None = None  # 'chat' or 'always': stop asking about this app
    always: bool = False  # "Always allow": save the tool or command prefix as allowed
    cached: bool = False  # Result was from cache
    auto: bool = False  # Auto-approved (low risk)
    error: str | None = None
    reviewed: bool = False  # Decided by the reviewer model, not a person
    review: dict | None = None  # The reviewer's verdict, when it was asked
    # Request Access: what a person allowed ('chat' or 'agent'). Only ever set
    # from a person's answer (their card, or their "Auto-approve everything"
    # setting), never by low-risk or unattended auto-approval.
    grant: str | None = None
    # Answered Yes by the person's "Auto-approve everything" setting
    # (safety/auto_approve_all.py). Counts as their answer.
    standing: bool = False


@dataclass
class ApprovalRequest:
    """Pending approval request."""
    id: str
    tool_call: ToolCall
    risk: RiskAssessment
    status: ApprovalStatus
    response_future: asyncio.Future


class ApprovalGate:
    """
    Manages human approval for risky operations.

    Supports multiple interfaces (CLI, WebSocket) and
    caches approvals to avoid repeated prompts.
    """

    def __init__(self, cache_dir: str = None):
        self.session_cache: set[str] = set()
        self.session_denied: set[str] = set()
        self.permanent_cache: set[str] = set()
        self.pending_requests: dict[str, ApprovalRequest] = {}
        self.request_counter = 0

        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / '.cognitrix'
        self._load_permanent_cache()

        logger.info("ApprovalGate initialized")

    def _cache_file(self) -> Path:
        return self.cache_dir / 'approval_cache.json'

    def _load_permanent_cache(self):
        cache_file = self._cache_file()
        if cache_file.exists():
            try:
                with open(cache_file, encoding='utf-8') as f:
                    data = json.load(f)
                    self.permanent_cache = set(data.get('approved', []))
                    logger.info(f"Loaded {len(self.permanent_cache)} permanent approvals")
            except Exception as e:
                logger.warning(f"Failed to load approval cache: {e}")

    def _save_permanent_cache(self):
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file(), 'w', encoding='utf-8') as f:
                json.dump({'approved': list(self.permanent_cache)}, f)
        except Exception as e:
            logger.error(f"Failed to save approval cache: {e}")

    def _hash_operation(self, tool_call: ToolCall) -> str:
        """Generate hash for operation caching."""
        content = f"{tool_call.tool_name}:{json.dumps(tool_call.params, sort_keys=True)}"
        return hashlib.sha256(content.encode()).hexdigest()

    async def check_approval(
        self,
        tool_call: ToolCall,
        risk: RiskAssessment,
        interface: str = 'cli',
        timeout: float = 300.0,
        scope: str | None = None,
        person_only: bool = False,
    ) -> ApprovalResult:
        """
        Check if operation requires and receives approval.

        Args:
            tool_call: The tool call to approve
            risk: Risk assessment
            interface: Interface type ('cli', 'web', 'ws', 'auto')
            timeout: Timeout in seconds for approval
            scope: Isolation scope for the approval cache (e.g. agent/user id) so
                   one caller's approval never auto-approves another's operation.
            person_only: The call only runs once a person approved it (the
                   full shell). The browser's auto-approve toggle and the
                   permanent cache don't answer it; a person is asked, unless
                   they approved this exact call for the session.

        Returns:
            ApprovalResult
        """
        # Actions in Cognitrix's own window (its settings, its approvals) are
        # answered by the person: no reviewer, bypass or "always allow this
        # tool" stands in. Their answer for the Cognitrix app itself still counts.
        own_app = 'own_app' in (risk.categories or [])

        # Non-interactive auto-approve (benchmarks / CI / unattended runs inside
        # a sandbox). Off by default; only enable in a throwaway environment.
        if _auto_approve_enabled():
            return ApprovalResult(approved=True, auto=True)

        # Auto-approve low risk
        if risk.risk_level == RiskLevel.LOW and not person_only:
            return ApprovalResult(approved=True, auto=True)

        # The person's "Auto-approve everything": a Yes on every card they
        # would be shown (see safety/auto_approve_all.py for what it skips).
        from cognitrix.safety import auto_approve_all

        categories = set(risk.categories or [])
        if await auto_approve_all.answers_for(interface):
            if not (categories & auto_approve_all.NEVER_CATEGORIES):
                return ApprovalResult(approved=True, standing=True,
                                      grant='chat' if 'access_request' in categories else None)
            if interface not in ('web', 'ws', 'websocket') and await auto_approve_all.unattended_here(interface):
                # A --dangerously-skip-permissions run has nobody to take
                # over: say so now rather than wait half an hour.
                return ApprovalResult(approved=False, error=auto_approve_all.NOBODY_WATCHING)

        # Durable workers have no interactive principal at execution time.
        # Interactive caches are intentionally ignored; a future durable
        # approval design must carry an explicit run-scoped authorization.
        if interface == 'task':
            from cognitrix.tasks.approvals import prior_decision, request_task_approval, task_turn_ctx

            ctx = task_turn_ctx.get(None)
            # A person's earlier answer for this call in this run wins; the
            # reviewer never overrides a recorded denial.
            if ctx and ctx.get('run_id'):
                earlier = await prior_decision(ctx, tool_call.tool_name, tool_call.params)
                if earlier is not None:
                    grant = 'chat' if earlier and 'access_request' in (risk.categories or []) else None
                    return ApprovalResult(approved=earlier, grant=grant)

            reviewed = None if own_app or person_only else await self._review(tool_call, risk, interface)
            if reviewed is not None:
                return reviewed

            # A run started by a person parks the call and asks them in the
            # web UI. Runs with nobody to ask (system/scheduler) are denied.
            if not ctx or not ctx.get('user_id') or not ctx.get('run_id'):
                return ApprovalResult(approved=False, error="approval_required")
            approved, error = await request_task_approval(
                ctx, tool_call.tool_name, tool_call.params, risk
            )
            # A person's yes on a Request Access allows it for this run.
            grant = 'chat' if approved and 'access_request' in (risk.categories or []) else None
            return ApprovalResult(approved=approved, error=error, grant=grant)

        # Cache keys are scoped so approvals don't leak across agents/users.
        scoped = f"{scope or 'global'}:{self._hash_operation(tool_call)}"

        # Taking over the person's screen, mouse and keyboard is asked every
        # time: no remembered answer, auto-approve or reviewer stands in.
        always_ask = bool(ALWAYS_ASK_CATEGORIES & set(risk.categories or []))
        # The person may be away for a while before they can take over.
        if 'person_takeover' in (risk.categories or []):
            timeout = max(timeout, TAKEOVER_ANSWER_TIMEOUT)
        # Connecting an app from the card takes a few minutes.
        if 'access_request' in (risk.categories or []):
            from cognitrix.tools.access import ANSWER_TIMEOUT
            timeout = max(timeout, ANSWER_TIMEOUT)

        if not person_only and not always_ask and scoped in self.permanent_cache:
            return ApprovalResult(approved=True, cached=True)

        # "Approve for session" is a person's answer, so it also covers the
        # full shell for the same command in this session.
        if not always_ask and scoped in self.session_cache:
            return ApprovalResult(approved=True, cached=True, remember=True)

        # The person allowed background actions in this app for this chat.
        app_key = self._app_key(tool_call, interface)
        if app_key and not person_only and not always_ask and app_key in self.session_cache:
            return ApprovalResult(approved=True, cached=True, remember=True)

        # The person allowed every full-shell command in this chat.
        # Only Bash: "allow all commands" never covers Coding Agent or Copy
        # Back, which are person-only too.
        shell_key = self._shell_key(scope, interface) if self._is_shell(risk) else None
        if person_only and shell_key and shell_key in self.session_cache:
            return ApprovalResult(approved=True, cached=True, remember=True)

        # "Always allow" answers: this tool for this agent (until the agent is
        # reloaded with it set to allow), or a saved command prefix.
        tool_key = self._tool_allow_key(scope, tool_call)
        chat_tool_key = self._chat_tool_allow_key(scope, tool_call, interface)
        if not person_only and not always_ask and not own_app and (
                tool_key in self.session_cache or (chat_tool_key and chat_tool_key in self.session_cache)):
            return ApprovalResult(approved=True, cached=True, remember=True)
        if person_only and not always_ask and await self._command_allowed(tool_call, interface):
            return ApprovalResult(approved=True, cached=True)

        # Web bypass mode approves everything anyway; don't spend a review.
        # A call a person already refused goes back to the person.
        web_ctx = web_turn_ctx.get(None)
        bypass = (
            not person_only and not always_ask and not own_app
            and interface in ('web', 'ws', 'websocket') and web_ctx and web_ctx.get('bypass')
        )
        # Nor for a call only a person may approve: a reviewer "allow" counts
        # as automatic, so the tool would refuse it and nobody would see a card.
        if (not bypass and not always_ask and not own_app and not person_only
                and scoped not in self.session_denied):
            reviewed = await self._review(tool_call, risk, interface)
            if reviewed is not None:
                return reviewed

        # Need explicit approval
        if interface == 'auto':
            return ApprovalResult(
                approved=False,
                error="Explicit approval required but auto mode enabled",
            )

        # Durable task workers are non-interactive and were denied above.
        if interface == 'cli':
            result = await self._cli_approval(tool_call, risk, timeout)
        elif interface in ('web', 'ws', 'websocket'):
            result = await self._web_approval(tool_call, risk, timeout, person_only=person_only, must_ask=always_ask,
                                              scope=scope, **({'no_bypass': True} if own_app else {}))
        elif interface == 'acp':
            approver = acp_approver.get(None)
            if approver is None:
                return ApprovalResult(approved=False, error='approval_required')
            try:
                result = await approver(tool_call, risk, person_only)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('Editor approval failed: %s', exc)
                return ApprovalResult(approved=False, error='approval_required')
        else:
            return ApprovalResult(approved=False, error=f"Unknown interface: {interface}")

        if not result.approved and not result.error:
            # The person said no; the reviewer must not approve it later.
            self.session_denied.add(scoped)

        # Request Access: a person's yes in the terminal or an editor allows
        # it in this chat. Auto-approval never grants (see tools/access.py).
        if (result.approved and not result.auto and not result.reviewed and result.grant is None
                and 'access_request' in (risk.categories or [])):
            result.grant = 'chat'

        if result.approved and getattr(result, 'always', False) and not always_ask:
            saved_for_agent = await self._save_always(tool_call, scope, interface, person_only)
            if not person_only:
                # The agent-wide key only when its owner changed the agent's
                # setting; otherwise the answer holds for this person's chat.
                if saved_for_agent:
                    self.session_cache.add(tool_key)
                elif chat_tool_key:
                    self.session_cache.add(chat_tool_key)

        # Cache if approved (scoped). The full shell is only ever remembered
        # for the session, never permanently.
        if result.approved and person_only and (result.permanent or result.remember):
            self.session_denied.discard(scoped)
            self.session_cache.add(scoped)
            if getattr(result, 'remember_shell', False) and shell_key:
                self.session_cache.add(shell_key)
        elif result.approved and always_ask:
            self.session_denied.discard(scoped)
        elif result.approved:
            self.session_denied.discard(scoped)
            if app_key and result.remember_app in ('chat', 'always'):
                self.session_cache.add(app_key)
                if result.remember_app == 'always':
                    await self._always_allow_app(tool_call.app, interface)
            if result.permanent:
                self.permanent_cache.add(scoped)
                self._save_permanent_cache()
            elif result.remember:
                self.session_cache.add(scoped)

        return result

    @staticmethod
    def _app_owner(interface: str) -> str | None:
        """Whose answer an app approval is: the signed-in person, or the CLI's."""
        if interface == 'cli':
            return 'local'
        if interface in ('web', 'ws', 'websocket'):
            return str((web_turn_ctx.get(None) or {}).get('user_key') or '') or None
        return None

    @classmethod
    def _app_key(cls, tool_call: ToolCall, interface: str) -> str | None:
        """Cache key for "allow this app in this chat" (any agent in the chat)."""
        if not tool_call.app:
            return None
        owner = cls._app_owner(interface)
        if not owner:
            return None
        session_id = '' if interface == 'cli' else str((web_turn_ctx.get(None) or {}).get('session_id') or '')
        if interface != 'cli' and not session_id:
            return None
        return f"computer_app:{tool_call.app}:{owner}:{session_id}"

    async def _always_allow_app(self, app: str, interface: str) -> None:
        owner = self._app_owner(interface)
        if not owner:
            return
        try:
            from cognitrix.computer import apps as computer_apps

            await computer_apps.set_level(owner, app, 'allow')
        except Exception:
            logger.exception("Could not save 'always allow' for %s", app)

    @staticmethod
    def _tool_allow_key(scope: str | None, tool_call: ToolCall) -> str:
        return f"tool_allow:{scope or 'global'}:{_normal_name(tool_call.tool_name)}"

    @staticmethod
    def _chat_tool_allow_key(scope: str | None, tool_call: ToolCall, interface: str) -> str | None:
        """"Always allow" on an agent the person doesn't own: this person, this chat."""
        if interface not in ('web', 'ws', 'websocket'):
            return None
        ctx = web_turn_ctx.get(None) or {}
        session_id = ctx.get('session_id')
        if not session_id:
            return None
        return (f"tool_allow:{scope or 'global'}:{ctx.get('user_key') or ''}:{session_id}:"
                f"{_normal_name(tool_call.tool_name)}")

    @staticmethod
    def _is_shell(risk: RiskAssessment) -> bool:
        """A command run in a shell: Bash's full shell, or a coding agent
        CLI's own command in ask mode. Not Coding Agent or Copy Back."""
        return 'shell' in (risk.categories or [])

    @staticmethod
    def _command(tool_call: ToolCall) -> str | None:
        if _normal_name(tool_call.tool_name) != 'bash':
            return None
        command = (tool_call.params or {}).get('command')
        return command if isinstance(command, str) and command.strip() else None

    @staticmethod
    def _computer() -> str:
        from cognitrix.safety.command_rules import computer_key
        try:
            from cognitrix.tools.utils import current_execution_context
            return computer_key(getattr(current_execution_context(), 'device', None))
        except Exception:
            return computer_key(None)

    async def _command_allowed(self, tool_call: ToolCall, interface: str) -> bool:
        command = self._command(tool_call)
        owner = self._app_owner(interface)
        if not command or not owner or owner == 'local':
            return False
        from cognitrix.safety import command_rules
        return await command_rules.allowed(owner, self._computer(), command)

    def _always_offer(self, tool_call: ToolCall, scope: str | None, person_only: bool,
                      must_ask: bool) -> dict | None:
        """What the card's "Always allow" button saves, or None for no button."""
        if must_ask or tool_call.app:
            return None  # an app has its own "always allow <app>"
        if person_only:
            from cognitrix.safety.command_rules import prefix_for
            command = self._command(tool_call)
            prefix = prefix_for(command) if command else None
            if not prefix:
                return None
            computer = self._computer()
            return {'kind': 'command', 'label': prefix, 'computer': computer}
        if not scope or ':' in str(tool_call.tool_name) or _normal_name(tool_call.tool_name) in _NEVER_AUTO_APPROVED:
            return None
        return {'kind': 'tool', 'label': str(tool_call.tool_name)}

    async def _save_always(self, tool_call: ToolCall, scope: str | None, interface: str,
                           person_only: bool) -> bool:
        """Save the answer. True when the agent's own setting now allows the tool."""
        owner = self._app_owner(interface)
        if not owner or owner == 'local':
            return False
        try:
            if person_only:
                from cognitrix.safety import command_rules
                command = self._command(tool_call)
                if command:
                    await command_rules.add(owner, self._computer(), command)
                return False
            if not scope:
                return False
            from cognitrix.models.agent import Agent
            agent = await Agent.get(str(scope))
            # Only the agent's owner changes its settings; on someone else's
            # agent the answer holds for this chat.
            if agent is None or str(getattr(agent, 'owner_id', '') or '') != str(owner):
                return False
            perms = dict(getattr(agent, 'tool_permissions', None) or {})
            perms[str(tool_call.tool_name)] = 'allow'
            await Agent.update_one({'id': agent.id}, {'tool_permissions': perms})
            return True
        except Exception:
            logger.exception("Could not save 'always allow' for %s", tool_call.tool_name)
            return False

    @staticmethod
    def _shell_key(scope: str | None, interface: str) -> str | None:
        """Cache key for "allow all shell commands in this chat"."""
        if interface not in ('web', 'ws', 'websocket'):
            return None
        ctx = web_turn_ctx.get(None) or {}
        session_id = ctx.get('session_id')
        if not session_id:
            return None
        return f"{scope or 'global'}:full_shell:{ctx.get('user_key') or ''}:{session_id}"

    async def _review(self, tool_call: ToolCall, risk: RiskAssessment,
                      interface: str) -> ApprovalResult | None:
        """Let the reviewer model settle the call when it is turned on.

        Returns an allow/block result, or None when a person should be asked
        as usual (reviewer off, unsure, or failed).
        """
        from cognitrix.safety import action_reviewer

        if not action_reviewer.should_review(risk, interface):
            return None
        verdict = await action_reviewer.review_action(tool_call.tool_name, tool_call.params, risk)
        await self._report_review(tool_call, risk, interface, verdict)
        if verdict.decision == action_reviewer.ALLOW:
            status_print(f"   Reviewer allowed it: {verdict.reason}")
            return ApprovalResult(approved=True, auto=True, reviewed=True, review=verdict.payload())
        if verdict.decision == action_reviewer.BLOCK:
            status_print(f"   Reviewer blocked it: {verdict.reason}")
            return ApprovalResult(
                approved=False,
                reviewed=True,
                review=verdict.payload(),
                error=f"blocked by the reviewer: {verdict.reason or 'no reason given'}",
            )
        status_print(f"   Reviewer is unsure, asking: {verdict.reason}")
        return None

    async def _report_review(self, tool_call: ToolCall, risk: RiskAssessment,
                             interface: str, verdict) -> None:
        """Record the verdict where people can see it (task run events)."""
        if interface != 'task':
            return
        from cognitrix.tasks.approvals import task_turn_ctx

        ctx = task_turn_ctx.get(None) or {}
        emit = ctx.get('emit')
        if emit is None:
            return
        try:
            await emit('action_reviewed', {
                'tool_name': tool_call.tool_name,
                'params': tool_call.params,
                'risk_level': risk.risk_level.value,
                'details': risk.details,
                **verdict.payload(),
            })
        except Exception:
            logger.exception("Could not record the reviewer's verdict")

    async def _cli_approval(self, tool_call: ToolCall, risk: RiskAssessment, timeout: float = 300.0) -> ApprovalResult:
        """Command-line approval prompt."""
        status_print(f"\n{'='*60}")
        status_print(f"⚠️  APPROVAL REQUIRED - {risk.risk_level.value.upper()} RISK")
        status_print(f"{'='*60}")
        status_print(f"\nOperation: {tool_call.tool_name}")
        status_print("Parameters:")
        status_print(json.dumps(tool_call.params, indent=2, ensure_ascii=False))
        status_print(f"\nRisk Categories: {', '.join(risk.categories)}")
        status_print(f"Details: {risk.details}")
        status_print(f"\n{'='*60}")

        try:
            # input() in an executor so the event loop keeps running, with the
            # gate's timeout honored (deny on expiry). ponytail: a timed-out
            # thread stays parked on stdin until process exit.
            loop = asyncio.get_running_loop()
            raw = await asyncio.wait_for(
                loop.run_in_executor(
                    None, input,
                    (f"\nApprove? [y=yes/n=no/s=session/a=always allow {tool_call.app}/p=permanent]: "
                     if tool_call.app else "\nApprove? [y=yes/n=no/s=session/p=permanent]: "),
                ),
                timeout=timeout,
            )
            response = raw.lower().strip()

            if response in ['y', 'yes']:
                return ApprovalResult(approved=True)
            elif response in ['s', 'session']:
                return ApprovalResult(approved=True, remember=True)
            elif response in ['a', 'always'] and tool_call.app:
                return ApprovalResult(approved=True, remember_app='always')
            elif response in ['p', 'permanent']:
                return ApprovalResult(approved=True, permanent=True)
            else:
                return ApprovalResult(approved=False)

        except TimeoutError:
            return ApprovalResult(approved=False, error=f"Approval timed out after {timeout}s")
        except (EOFError, KeyboardInterrupt):
            return ApprovalResult(approved=False, error="User interrupted")

    async def _web_approval(
        self,
        tool_call: ToolCall,
        risk: RiskAssessment,
        timeout: float,
        person_only: bool = False,
        must_ask: bool = False,
        scope: str | None = None,
        no_bypass: bool = False,
    ) -> ApprovalResult:
        """Interactive approval over the SSE chat stream.

        Emits an `approval_request` event to the browser and blocks on a future
        that the `POST /agents/approval` endpoint resolves. Bypass mode
        auto-approves. With no bound emit channel (e.g. the programmatic generate
        path) it denies, matching the prior behaviour.
        """
        global _web_request_counter
        ctx = web_turn_ctx.get(None)

        if (
            ctx and ctx.get('bypass') and not person_only and not must_ask and not no_bypass
            and _normal_name(tool_call.tool_name) not in _NEVER_AUTO_APPROVED
        ):
            # Auto-approve tool calls only — the bash whitelist / sandbox-shell
            # bypass is never enabled from the web.
            return ApprovalResult(approved=True, auto=True)

        emit = ctx.get('emit') if ctx else None
        if emit is None:
            return ApprovalResult(
                approved=False,
                error=(
                    f"'{tool_call.tool_name}' requires approval ({risk.risk_level.value} risk); "
                    "no interactive approval channel is available for this request."
                ),
            )

        _web_request_counter += 1
        # Random, so an id never repeats after a restart: chat apps remember
        # which approvals they were sent by id.
        request_id = f"web-approval-{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        session_id = str(ctx.get('session_id') or '')
        always = self._always_offer(tool_call, scope, person_only, must_ask)
        _WEB_PENDING[request_id] = {
            'future': future, 'user_key': ctx.get('user_key'), 'person_only': person_only,
            # A full-shell Bash command: the only kind "allow all commands in
            # this chat" may answer (Coding Agent is person-only too).
            'full_shell': person_only and self._is_shell(risk),
            'app': tool_call.app, 'always': always, 'access': tool_call.access,
            # What the Inbox, notifications and chat apps show about it.
            'tool_name': tool_call.tool_name,
            'params': tool_call.params,
            'risk_level': risk.risk_level.value,
            'details': risk.details,
            'session_id': session_id,
            'agent_name': str(ctx.get('agent_name') or ''),
            'href': str(ctx.get('href') or (f'/home?chat={session_id}' if session_id else '/inbox')),
            # A chat app that started the turn already shows the buttons.
            'origin': str(ctx.get('origin') or ''),
            'created': time.monotonic(),
            'created_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
            'seen_at': None,  # last time an open chat page showed the card
        }
        from cognitrix.safety import approval_alerts
        approval_alerts.schedule(request_id)

        try:
            await emit({
                'type': 'approval_request',
                'request_id': request_id,
                'tool_name': tool_call.tool_name,
                'params': tool_call.params,
                'risk_level': risk.risk_level.value,
                'categories': risk.categories,
                'details': risk.details,
                'session_id': ctx.get('session_id'),
                # A full-shell command: the card offers "allow all commands
                # in this chat".
                'full_shell': person_only and self._is_shell(risk),
                # Asked every time (a screen takeover): no "for session" button.
                **({'always_ask': True} if must_ask else {}),
                # The agent asks the person to take over its computer: the
                # card's Take over opens that desktop with them in control.
                **({'takeover': _takeover_target(tool_call, scope)}
                   if 'person_takeover' in (risk.categories or []) else {}),
                # Request Access: what the agent asks for, and the choices
                # (allow in this chat, add to the agent, connect the app).
                **({'access': tool_call.access} if tool_call.access else {}),
                # A background action in an app window: the card offers
                # "allow <app> in this chat" and "always allow <app>".
                **({'app': {'key': tool_call.app, 'label': _app_label(tool_call.app)}} if tool_call.app else {}),
                # "Always allow": this tool for this agent, or commands that
                # start like this one on this computer.
                **({'always': always} if always else {}),
            })
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            return ApprovalResult(approved=False, error=f"Approval timed out after {timeout}s")
        except Exception as e:
            logger.exception("Web approval failed")
            return ApprovalResult(approved=False, error=str(e))
        finally:
            _WEB_PENDING.pop(request_id, None)
            # It may have been answered from a notification or a chat app:
            # tell an open chat page to drop the card.
            try:
                await emit({'type': 'approval_resolved', 'request_id': request_id,
                            'session_id': ctx.get('session_id')})
            except Exception:
                pass

    async def _websocket_approval(
        self,
        tool_call: ToolCall,
        risk: RiskAssessment,
        timeout: float
    ) -> ApprovalResult:
        """WebSocket-based approval for Web UI."""
        # Generate request ID
        self.request_counter += 1
        request_id = f"approval_{self.request_counter}"

        # Create future for response
        future = asyncio.get_running_loop().create_future()

        # Store request
        request = ApprovalRequest(
            id=request_id,
            tool_call=tool_call,
            risk=risk,
            status=ApprovalStatus.PENDING,
            response_future=future
        )
        self.pending_requests[request_id] = request

        # TODO: Send request to WebSocket client
        # This would be implemented where WebSocket is available
        logger.info(f"WebSocket approval requested: {request_id}")

        try:
            # Wait for response
            result = await asyncio.wait_for(future, timeout=timeout)
            del self.pending_requests[request_id]
            return result

        except TimeoutError:
            request.status = ApprovalStatus.TIMEOUT
            del self.pending_requests[request_id]
            return ApprovalResult(
                approved=False,
                error=f"Approval timeout after {timeout}s"
            )

    def resolve_pending(self, request_id: str, approved: bool, remember: bool = False, permanent: bool = False):
        """Resolve a pending approval request (called from WebSocket handler)."""
        request = self.pending_requests.get(request_id)
        if request and not request.response_future.done():
            result = ApprovalResult(
                approved=approved,
                remember=remember,
                permanent=permanent
            )
            request.response_future.set_result(result)

    def clear_session_cache(self):
        """Clear session-level approvals."""
        self.session_cache.clear()
        self.session_denied.clear()
        logger.info("Session approval cache cleared")

    def clear_permanent_cache(self):
        """Clear permanent approvals."""
        self.permanent_cache.clear()
        self._save_permanent_cache()
        logger.info("Permanent approval cache cleared")

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        return {
            'session_cache_size': len(self.session_cache),
            'permanent_cache_size': len(self.permanent_cache),
            'pending_requests': len(self.pending_requests)
        }
