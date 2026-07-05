"""Completion webhooks for API-started task runs.

The payload is signed with the registering key's webhook_secret:
    X-Cognitrix-Timestamp: <unix seconds>
    X-Cognitrix-Signature: sha256=HMAC_SHA256("{timestamp}.{body}", secret)
Receivers should recompute the HMAC with hmac.compare_digest and reject stale
timestamps — the signed timestamp is the replay protection.

notify_completion NEVER raises: a webhook must not be able to fail a task run.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger('cognitrix.log')

ATTEMPTS = 3
ATTEMPT_TIMEOUT = 10.0
TOTAL_BUDGET = float(os.getenv('COGNITRIX_WEBHOOK_BUDGET', '15'))
BACKOFFS = (1.0, 2.0, 4.0)


def _allow_private() -> bool:
    return os.getenv('COGNITRIX_WEBHOOK_ALLOW_PRIVATE', '').strip().lower() in ('1', 'true', 'yes')


def check_callback_url(url: str) -> str | None:
    """SSRF guard. Returns a rejection reason, or None when the URL is OK.

    Blocking (does DNS) — call via asyncio.to_thread from async code. Residual
    gap: the host is re-resolved by httpx at send time, so a DNS-rebinding
    attacker can still swap the record between check and send (documented in
    the spec; acceptable for v1).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return 'callback_url must be http or https'
    host = parsed.hostname
    if not host:
        return 'callback_url has no host'
    if _allow_private():
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return 'callback_url host does not resolve'
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            return ('callback_url resolves to a private/loopback address '
                    '(set COGNITRIX_WEBHOOK_ALLOW_PRIVATE=1 to allow)')
    return None


def sign(body: str, secret: str, timestamp: str) -> str:
    digest = hmac.new(secret.encode('utf-8'), f'{timestamp}.{body}'.encode('utf-8'), hashlib.sha256)
    return f'sha256={digest.hexdigest()}'


async def notify_completion(task, run) -> bool:
    """POST the run's terminal state to task.callback_url. Returns True on a
    2xx delivery; False (with logging) on anything else. Never raises."""
    try:
        url = getattr(task, 'callback_url', None)
        key_id = getattr(task, 'callback_key_id', None)
        if not url or not key_id or run is None:
            return False

        from cognitrix.models.api_key import APIKey
        key = await APIKey.get(key_id)
        if key is None or key.revoked or key.is_expired():
            logger.info("Webhook for task %s skipped: key %s missing/revoked/expired", task.id, key_id)
            return False

        reason = await asyncio.to_thread(check_callback_url, url)
        if reason:
            logger.warning("Webhook for task %s blocked: %s", task.id, reason)
            return False

        # Re-read the run — an externally finalized status (force-cancel) is
        # authoritative over the in-memory record.
        run_cls = type(run)
        fresh = await run_cls.get(run.id) if getattr(run, 'id', None) else None
        current = fresh or run

        status = getattr(current.status, 'value', current.status)
        body = json.dumps({
            'task_id': task.id,
            'run_id': current.id,
            'status': status,
            'result': current.result,
            'error': current.error,
            'completed_at': current.completed_at,
        })
        timestamp = str(int(time.time()))
        headers = {
            'Content-Type': 'application/json',
            'X-Cognitrix-Timestamp': timestamp,
            'X-Cognitrix-Signature': sign(body, key.webhook_secret, timestamp),
        }

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=ATTEMPT_TIMEOUT, follow_redirects=False) as client:
            for attempt in range(ATTEMPTS):
                try:
                    response = await client.post(url, content=body, headers=headers)
                    if 200 <= response.status_code < 300:
                        return True
                    logger.warning("Webhook for task %s got HTTP %s (attempt %d/%d)",
                                   task.id, response.status_code, attempt + 1, ATTEMPTS)
                except httpx.HTTPError as exc:
                    logger.warning("Webhook for task %s failed: %s (attempt %d/%d)",
                                   task.id, type(exc).__name__, attempt + 1, ATTEMPTS)
                if attempt + 1 >= ATTEMPTS or time.monotonic() - started > TOTAL_BUDGET:
                    break
                await asyncio.sleep(BACKOFFS[min(attempt, len(BACKOFFS) - 1)])
        logger.error("Webhook for task %s not delivered after %d attempts", task.id, ATTEMPTS)
        return False
    except Exception:
        logger.exception("Webhook delivery for task %s blew up", getattr(task, 'id', '?'))
        return False
