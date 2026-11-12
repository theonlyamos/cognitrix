"""Human-in-the-loop approval system for risky operations."""

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from cognitrix.common.encoding import status_print
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

logger = logging.getLogger('cognitrix.log')


# Tools a person must answer even with the browser's auto-approve on:
# giving an agent a new folder or write access widens what it can touch.
_NEVER_AUTO_APPROVED = frozenset({'request_folder'})


def _normal_name(name: str) -> str:
    return str(name or '').strip().lower().replace(' ', '_')


def _auto_approve_enabled() -> bool:
    return os.getenv('COGNITRIX_AUTO_APPROVE', '').strip().lower() in ('1', 'true', 'yes')


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
_web_request_counter = 0


def resolve_web_approval(request_id: str, approved: bool, user_key: str | None = None,
                         remember: bool = False, remember_shell: bool = False,
                         remember_app: str | None = None) -> bool:
    """Resolve a pending browser approval. Returns False if unknown or not the owner.

    `remember_app` (only for a background action in an app window) is 'chat'
    to stop asking about that app in this chat, or 'always' to save it as
    the person's standing answer.

    `remember_shell` (only for a full-shell command) lets every full-shell
    command in that chat run without asking again until the app restarts.

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
            remember_shell=bool(approved and remember_shell and entry.get('person_only')),
            remember_app=(remember_app if approved and entry.get('app')
                          and remember_app in ('chat', 'always') else None),
        ))
    return True

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
    cached: bool = False  # Result was from cache
    auto: bool = False  # Auto-approved (low risk)
    error: str | None = None
    reviewed: bool = False  # Decided by the reviewer model, not a person
    review: dict | None = None  # The reviewer's verdict, when it was asked


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
        # Non-interactive auto-approve (benchmarks / CI / unattended runs inside
        # a sandbox). Off by default; only enable in a throwaway environment.
        if _auto_approve_enabled():
            return ApprovalResult(approved=True, auto=True)

        # Auto-approve low risk
        if risk.risk_level == RiskLevel.LOW and not person_only:
            return ApprovalResult(approved=True, auto=True)

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
                    return ApprovalResult(approved=earlier)

            reviewed = await self._review(tool_call, risk, interface)
            if reviewed is not None:
                return reviewed

            # A run started by a person parks the call and asks them in the
            # web UI. Runs with nobody to ask (system/scheduler) are denied.
            if not ctx or not ctx.get('user_id') or not ctx.get('run_id'):
                return ApprovalResult(approved=False, error="approval_required")
            approved, error = await request_task_approval(
                ctx, tool_call.tool_name, tool_call.params, risk
            )
            return ApprovalResult(approved=approved, error=error)

        # Cache keys are scoped so approvals don't leak across agents/users.
        scoped = f"{scope or 'global'}:{self._hash_operation(tool_call)}"

        # Taking over the person's screen, mouse and keyboard is asked every
        # time: no remembered answer, auto-approve or reviewer stands in.
        always_ask = bool({'screen_takeover', 'person_takeover'} & set(risk.categories or []))

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
        shell_key = self._shell_key(scope, interface)
        if person_only and shell_key and shell_key in self.session_cache:
            return ApprovalResult(approved=True, cached=True, remember=True)

        # Web bypass mode approves everything anyway; don't spend a review.
        # A call a person already refused goes back to the person.
        web_ctx = web_turn_ctx.get(None)
        bypass = (
            not person_only and not always_ask
            and interface in ('web', 'ws', 'websocket') and web_ctx and web_ctx.get('bypass')
        )
        if not bypass and not always_ask and scoped not in self.session_denied:
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
            result = await self._web_approval(tool_call, risk, timeout, person_only=person_only, must_ask=always_ask)
        else:
            return ApprovalResult(approved=False, error=f"Unknown interface: {interface}")

        if not result.approved and not result.error:
            # The person said no; the reviewer must not approve it later.
            self.session_denied.add(scoped)

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
            ctx and ctx.get('bypass') and not person_only and not must_ask
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
        _WEB_PENDING[request_id] = {
            'future': future, 'user_key': ctx.get('user_key'), 'person_only': person_only,
            'app': tool_call.app,
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
                'full_shell': person_only,
                # Asked every time (a screen takeover): no "for session" button.
                **({'always_ask': True} if must_ask else {}),
                # A background action in an app window: the card offers
                # "allow <app> in this chat" and "always allow <app>".
                **({'app': {'key': tool_call.app, 'label': _app_label(tool_call.app)}} if tool_call.app else {}),
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
