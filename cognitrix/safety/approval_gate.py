"""Human-in-the-loop approval system for risky operations."""

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

logger = logging.getLogger('cognitrix.log')


def _auto_approve_enabled() -> bool:
    return os.getenv('COGNITRIX_AUTO_APPROVE', '').strip().lower() in ('1', 'true', 'yes')

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

    def dict(self):
        return {'tool_name': self.tool_name, 'params': self.params}


@dataclass
class ApprovalResult:
    """Result of an approval request."""
    approved: bool
    remember: bool = False  # Remember for this session
    permanent: bool = False  # Remember permanently
    cached: bool = False  # Result was from cache
    auto: bool = False  # Auto-approved (low risk)
    error: str | None = None


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
                with open(cache_file) as f:
                    data = json.load(f)
                    self.permanent_cache = set(data.get('approved', []))
                    logger.info(f"Loaded {len(self.permanent_cache)} permanent approvals")
            except Exception as e:
                logger.warning(f"Failed to load approval cache: {e}")

    def _save_permanent_cache(self):
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file(), 'w') as f:
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

        Returns:
            ApprovalResult
        """
        # Non-interactive auto-approve (benchmarks / CI / unattended runs inside
        # a sandbox). Off by default; only enable in a throwaway environment.
        if _auto_approve_enabled():
            return ApprovalResult(approved=True, auto=True)

        # Auto-approve low risk
        if risk.risk_level == RiskLevel.LOW:
            return ApprovalResult(approved=True, auto=True)

        # Cache keys are scoped so approvals don't leak across agents/users.
        scoped = f"{scope or 'global'}:{self._hash_operation(tool_call)}"

        if scoped in self.permanent_cache:
            return ApprovalResult(approved=True, cached=True)

        if scoped in self.session_cache:
            return ApprovalResult(approved=True, cached=True, remember=True)

        # Need explicit approval
        if interface == 'auto':
            return ApprovalResult(
                approved=False,
                error="Explicit approval required but auto mode enabled"
            )

        # Request approval through interface. 'task' (planner steps) runs in
        # the same console as the CLI, so it shares the CLI prompt.
        if interface in ('cli', 'task'):
            result = await self._cli_approval(tool_call, risk, timeout)
        elif interface in ('web', 'ws', 'websocket'):
            # Interim: there is no interactive approval channel wired to the web
            # frontend yet, so deny risky operations with a clear message rather
            # than blocking on server-side input(). (Enabling live approval is a
            # frontend feature; see plan H3.3.)
            return ApprovalResult(
                approved=False,
                error=(
                    f"'{tool_call.tool_name}' requires approval ({risk.risk_level.value} risk); "
                    "interactive approval is not yet available over the web interface."
                ),
            )
        else:
            return ApprovalResult(approved=False, error=f"Unknown interface: {interface}")

        # Cache if approved (scoped)
        if result.approved:
            if result.permanent:
                self.permanent_cache.add(scoped)
                self._save_permanent_cache()
            elif result.remember:
                self.session_cache.add(scoped)

        return result

    async def _cli_approval(self, tool_call: ToolCall, risk: RiskAssessment, timeout: float = 300.0) -> ApprovalResult:
        """Command-line approval prompt."""
        print(f"\n{'='*60}")
        print(f"⚠️  APPROVAL REQUIRED - {risk.risk_level.value.upper()} RISK")
        print(f"{'='*60}")
        print(f"\nOperation: {tool_call.tool_name}")
        print("Parameters:")
        print(json.dumps(tool_call.params, indent=2))
        print(f"\nRisk Categories: {', '.join(risk.categories)}")
        print(f"Details: {risk.details}")
        print(f"\n{'='*60}")

        try:
            # input() in an executor so the event loop keeps running, with the
            # gate's timeout honored (deny on expiry). ponytail: a timed-out
            # thread stays parked on stdin until process exit.
            loop = asyncio.get_running_loop()
            raw = await asyncio.wait_for(
                loop.run_in_executor(
                    None, input, "\nApprove? [y=yes/n=no/s=session/p=permanent]: "
                ),
                timeout=timeout,
            )
            response = raw.lower().strip()

            if response in ['y', 'yes']:
                return ApprovalResult(approved=True)
            elif response in ['s', 'session']:
                return ApprovalResult(approved=True, remember=True)
            elif response in ['p', 'permanent']:
                return ApprovalResult(approved=True, permanent=True)
            else:
                return ApprovalResult(approved=False)

        except TimeoutError:
            return ApprovalResult(approved=False, error=f"Approval timed out after {timeout}s")
        except (EOFError, KeyboardInterrupt):
            return ApprovalResult(approved=False, error="User interrupted")

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
        future = asyncio.get_event_loop().create_future()

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
