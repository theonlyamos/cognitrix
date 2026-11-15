"""Send Email: an agent writes from its own email address (agent email,
``cognitrix/agent_email``). Replies come back into the same chat when they
are from an allowed sender.

Added to a turn only when the person connected a mailbox and the agent's
address is on (see ``Session._run_turn``). Its name says "send", so the
approval policy asks before each email goes out.
"""
from __future__ import annotations

from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome, current_execution_context

EMAIL_TOOL_CATEGORY = 'email'
MAX_BODY = 50_000


async def agent_has_address(user_key: str | None, agent_id: str | None) -> bool:
    if not user_key or not agent_id:
        return False
    from cognitrix.agent_email.models import AgentAddress, EmailAccount

    account = await EmailAccount.find_one({'user_id': str(user_key)})
    if account is None or not account.enabled or not account.smtp_host:
        return False
    row = await AgentAddress.find_one({'account_id': account.id, 'agent_id': str(agent_id)})
    return row is not None and bool(row.enabled)


@tool(category=EMAIL_TOOL_CATEGORY, retryable=False, max_attempts=1)
async def send_email(to: str, subject: str, body: str) -> ToolOutcome:
    """Send an email from your own email address.

    Use it when the person asks you to email someone, or to send them a result by email.
    Replies to it come back to this chat if they are from someone allowed to give you work.

    Args:
        to: One recipient address, like name@example.com.
        subject: The subject line.
        body: The message, as plain text.
    """
    from cognitrix.agent_email import mail
    from cognitrix.agent_email.models import EmailAccount
    from cognitrix.agent_email.service import EmailService, get_service

    ctx = current_execution_context()
    to = (to or '').strip()
    if not mail.valid_address(to):
        return ToolOutcome.failure('bad_address', 'Give one email address, like name@example.com.')
    if not (body or '').strip():
        return ToolOutcome.failure('empty', 'The email has no text.')
    if not await agent_has_address(ctx.user_id, ctx.agent_id):
        return ToolOutcome.failure('no_address', 'You have no email address. The person can set one up in '
                                                 'Settings > Agent email.')
    from cognitrix.agents import access as agent_access

    agent = await agent_access.find_visible_agent(str(ctx.user_id), agent_id=str(ctx.agent_id))
    account = await EmailAccount.find_one({'user_id': str(ctx.user_id)})
    if agent is None or account is None:
        return ToolOutcome.failure('no_address', 'You have no email address.')
    service = get_service() or EmailService()
    try:
        await service.send_mail(account, agent, to=to, subject=(subject or '').strip()[:200] or '(no subject)',
                                body=body[:MAX_BODY], session_id=str(ctx.session_id or ''), auto=False)
    except mail.MailError as exc:
        return ToolOutcome.failure('send_failed', str(exc))
    return ToolOutcome.success(f'Sent to {to}.')
