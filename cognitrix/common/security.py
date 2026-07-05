import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from ..models import User
from ..models.api_key import KEY_PREFIX, APIKey, hash_secret
from .constants import JWT_ALGORITHM, JWT_SECRET_KEY

load_dotenv()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (used instead of the unmaintained passlib)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str | None = None

async def get_user(email: str) -> User | None:
    return await User.find_one({'email': email})


async def authenticate(email: str, password: str):
    user = await get_user(email)
    if not user:
        return False
    if not verify_password(password, user.password):
        return False
    return user

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
        TokenData(username=username)
    except jwt.PyJWTError:
        raise credentials_exception from None

    if not isinstance(username, str):
        raise credentials_exception

    user = await get_user(email=username)
    if user is None:
        raise credentials_exception

    del user.password
    return user


# Matched case-insensitively; includes common secret-bearing HTTP header names
# so keys inside extra_headers (Authorization, x-api-key, ...) are also blanked.
_SECRET_KEYS = {
    'api_key', 'apikey', 'api-key', 'password', 'token', 'access_token', 'secret',
    'authorization', 'x-api-key', 'helicone-auth', 'bearer',
}


def redact_secrets(data):
    """Recursively blank out secret-bearing keys (api_key, Authorization, etc.)
    in a dict/list before returning it to a client. Provider API keys must never
    leave the server in an API response."""
    if isinstance(data, dict):
        return {
            k: ('***' if (isinstance(k, str) and k.lower() in _SECRET_KEYS and v) else redact_secrets(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact_secrets(v) for v in data]
    return data


def identity(payload):
    user_id = payload['identity']
    return User.get(user_id)


async def verify_token(token: str) -> User | None:
    """Verify a JWT token and return the user."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        email: str | None = payload.get("sub")
        if email is None:
            return None
        user = await get_user(email=email)
        if user is None:
            return None
        return user
    except jwt.PyJWTError:
        return None


# --- Unified auth (session JWT or API key) ---------------------------------

_credentials_401 = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass
class AuthContext:
    """Resolved caller identity. A JWT session has api_key=None and passes
    every scope/allowlist check; an API key is narrowed by its record."""

    user: User
    api_key: APIKey | None = None

    def has_scope(self, scope: str) -> bool:
        return self.api_key is None or self.api_key.has_scope(scope)

    def agent_allowed(self, agent_id: str) -> bool:
        return self.api_key is None or self.api_key.agent_allowed(agent_id)

    def team_allowed(self, team_id: str) -> bool:
        return self.api_key is None or self.api_key.team_allowed(team_id)


def _extract_credential(request: Request) -> str | None:
    auth = request.headers.get('Authorization', '')
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return request.headers.get('X-API-Key') or None


# Throttle last_used_at writes to one per key per interval — every API call
# hits this dependency and sqlite doesn't need a write per request.
_LAST_USED_STAMP_INTERVAL = 60.0
_last_used_stamped: dict[str, float] = {}


async def _stamp_last_used(key: APIKey) -> None:
    now = time.monotonic()
    prev = _last_used_stamped.get(key.id)
    if prev is not None and now - prev < _LAST_USED_STAMP_INTERVAL:
        return
    _last_used_stamped[key.id] = now
    stamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    await APIKey.update_one({'id': key.id}, {'last_used_at': stamp})


async def _resolve_api_key(secret: str) -> AuthContext:
    key = await APIKey.find_one({'key_hash': hash_secret(secret)})
    # Uniform 401 — never reveal whether a key exists vs is revoked/expired.
    if key is None or key.revoked or key.is_expired():
        raise _credentials_401
    user = await User.get(key.user_id)
    if user is None:
        raise _credentials_401
    from .rate_limit import check_rate_limit
    check_rate_limit(key)
    await _stamp_last_used(key)
    if getattr(user, 'password', None):
        del user.password
    return AuthContext(user=user, api_key=key)


async def _resolve_jwt(token: str) -> AuthContext:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        email = payload.get("sub")
    except jwt.PyJWTError:
        raise _credentials_401 from None
    if not isinstance(email, str):
        raise _credentials_401
    user = await get_user(email=email)
    if user is None:
        raise _credentials_401
    if getattr(user, 'password', None):
        del user.password
    return AuthContext(user=user)


async def get_auth_context(request: Request) -> AuthContext:
    """Single auth entry point: session JWT or API key, both from headers.

    Consume via Depends(get_auth_context) ONLY — FastAPI caches it per
    request; a direct call would double-charge the rate limit.
    """
    credential = _extract_credential(request)
    if not credential:
        raise _credentials_401
    if credential.startswith(KEY_PREFIX):
        return await _resolve_api_key(credential)
    return await _resolve_jwt(credential)


def require(*scopes: str):
    """Dependency: caller must hold every listed scope (JWT always passes)."""

    async def dependency(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        missing = [s for s in scopes if not ctx.has_scope(s)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key missing required scope(s): {', '.join(missing)}",
            )
        return ctx

    return dependency


async def crud_scope(request: Request, ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Router-level scope guard for CRUD routers: GET/HEAD need 'read',
    everything else 'write'. Execute/invoke routes must NOT sit behind this —
    they live on dedicated routers with explicit require(...) deps."""
    scope = 'read' if request.method in ('GET', 'HEAD') else 'write'
    if not ctx.has_scope(scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key missing required scope: {scope}",
        )
    return ctx


async def jwt_only(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Reject API keys — for browser-session plumbing (SSE action queues) and
    key management, where key access would bypass scopes or escalate."""
    if ctx.api_key is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires a browser session (API keys not accepted)",
        )
    return ctx
