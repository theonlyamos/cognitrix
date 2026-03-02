"""Human-in-the-loop approval system for risky operations."""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable

from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

logger = logging.getLogger('cognitrix.log')


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
    error: Optional[str] = None


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
    
    def __init__(self):
        self.session_cache: set[str] = set()  # Approved in this session
        self.permanent_cache: set[str] = set()  # Permanently approved
        self.pending_requests: dict[str, ApprovalRequest] = {}
        self.request_counter = 0
        
        logger.info("ApprovalGate initialized")
    
    def _hash_operation(self, tool_call: ToolCall) -> str:
        """Generate hash for operation caching."""
        content = f"{tool_call.tool_name}:{json.dumps(tool_call.params, sort_keys=True)}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    async def check_approval(
        self,
        tool_call: ToolCall,
        risk: RiskAssessment,
        interface: str = 'cli',
        timeout: float = 300.0
    ) -> ApprovalResult:
        """
        Check if operation requires and receives approval.
        
        Args:
            tool_call: The tool call to approve
            risk: Risk assessment
            interface: Interface type ('cli', 'websocket', 'auto')
            timeout: Timeout in seconds for approval
            
        Returns:
            ApprovalResult
        """
        # Auto-approve low risk
        if risk.risk_level == RiskLevel.LOW:
            return ApprovalResult(approved=True, auto=True)
        
        # Check caches
        op_hash = self._hash_operation(tool_call)
        
        if op_hash in self.permanent_cache:
            return ApprovalResult(approved=True, cached=True)
        
        if op_hash in self.session_cache:
            return ApprovalResult(approved=True, cached=True, remember=True)
        
        # Need explicit approval
        if interface == 'auto':
            return ApprovalResult(
                approved=False,
                error="Explicit approval required but auto mode enabled"
            )
        
        # Request approval through interface
        if interface == 'cli':
            result = await self._cli_approval(tool_call, risk)
        elif interface == 'websocket':
            result = await self._websocket_approval(tool_call, risk, timeout)
        else:
            return ApprovalResult(approved=False, error=f"Unknown interface: {interface}")
        
        # Cache if approved
        if result.approved:
            if result.permanent:
                self.permanent_cache.add(op_hash)
            elif result.remember:
                self.session_cache.add(op_hash)
        
        return result
    
    async def _cli_approval(self, tool_call: ToolCall, risk: RiskAssessment) -> ApprovalResult:
        """Command-line approval prompt."""
        print(f"\n{'='*60}")
        print(f"⚠️  APPROVAL REQUIRED - {risk.risk_level.value.upper()} RISK")
        print(f"{'='*60}")
        print(f"\nOperation: {tool_call.tool_name}")
        print(f"Parameters:")
        print(json.dumps(tool_call.params, indent=2))
        print(f"\nRisk Categories: {', '.join(risk.categories)}")
        print(f"Details: {risk.details}")
        print(f"\n{'='*60}")
        
        try:
            response = input(
                "\nApprove? [y=yes/n=no/s=session/p=permanent]: "
            ).lower().strip()
            
            if response in ['y', 'yes']:
                return ApprovalResult(approved=True)
            elif response in ['s', 'session']:
                return ApprovalResult(approved=True, remember=True)
            elif response in ['p', 'permanent']:
                return ApprovalResult(approved=True, permanent=True)
            else:
                return ApprovalResult(approved=False)
                
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
            
        except asyncio.TimeoutError:
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
        logger.info("Permanent approval cache cleared")
    
    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        return {
            'session_cache_size': len(self.session_cache),
            'permanent_cache_size': len(self.permanent_cache),
            'pending_requests': len(self.pending_requests)
        }
