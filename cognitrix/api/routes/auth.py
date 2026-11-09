import hashlib
import logging
import os
import secrets
import time
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cognitrix.common.constants import JWT_ACCESS_TOKEN_EXPIRE_MINUTES
from cognitrix.common.security import (
    AuthContext,
    Token,
    authenticate,
    create_access_token,
    get_auth_context,
    get_current_user,
    jwt_only,
    verify_password,
)
from cognitrix.common.utils import Utils
from cognitrix.models import User

logger = logging.getLogger('cognitrix.log')

RESET_TOKEN_MINUTES = 60


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


async def _signup_open() -> bool:
    allow = os.getenv('COGNITRIX_ALLOW_SIGNUP', '').strip().lower()
    if allow in ('0', 'false', 'no'):
        return False
    if allow in ('1', 'true', 'yes'):
        return True
    return not await User.find_one({})


auth_api = APIRouter(
    prefix='/auth'
)

# Secret key for JWT encoding/decoding


class LoginForm(BaseModel):
    username: str
    password: str


@auth_api.post("/login", response_model=Token)
async def login(form_data: LoginForm):
    user = await authenticate(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=float(JWT_ACCESS_TOKEN_EXPIRE_MINUTES))
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    user_data = user.json()
    # Never return the password hash to the client.
    if isinstance(user_data, dict):
        for secret in ('password', 'reset_token_hash', 'reset_expires'):
            user_data.pop(secret, None)
        from cognitrix.agents.access import is_admin

        user_data['is_admin'] = await is_admin(user)

    return JSONResponse({"user": user_data, "access_token": access_token, "token_type": "bearer"})

@auth_api.post("/signup")
async def signup(new_user: User, request: Request):
    # Registration is closed by default once the first account exists: the
    # first person to sign up (the owner, and the default admin) always can,
    # so a fresh install still works. COGNITRIX_ALLOW_SIGNUP=true opens it to
    # everyone; =false closes it even for the first account.
    invite = await _usable_invite(request.query_params.get('invite'), new_user.email)
    if not await _signup_open() and invite is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Open registration is disabled"
        )
    if not Utils.email_is_valid(new_user.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address"
        )
    if not new_user.password or len(new_user.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters"
        )

    existing_user = await User.find_one({'email': new_user.email})

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    hashed_password = Utils.hash_password(new_user.password)
    new_user.password = hashed_password
    # Never from the request body: these are set by the server only.
    new_user.role = invite.role if invite is not None else None
    new_user.disabled = None
    new_user.reset_token_hash = None
    new_user.reset_expires = None
    new_user.tokens_valid_after = None

    await new_user.save()
    if invite is not None:
        from cognitrix.models.invite import Invite

        await Invite.update_one({'id': invite.id}, {'used_at': int(time.time()), 'used_by': new_user.email})
    return {"message": "User created successfully"}


async def _usable_invite(token: str | None, email: str):
    """The invite this token names, if it is unused, unexpired and meant for this email."""
    if not token:
        return None
    from cognitrix.models.invite import Invite

    invite = await Invite.find_one({'token_hash': _hash_token(token)})
    if invite is None or invite.used_at or int(invite.expires_at or 0) < int(time.time()):
        return None
    if invite.email and invite.email.strip().lower() != str(email or '').strip().lower():
        return None
    return invite


@auth_api.get("/options")
async def auth_options(invite: str | None = None):
    """What the sign-in pages can offer. Public."""
    from cognitrix.common import email as mailer

    valid_invite = False
    if invite:
        from cognitrix.models.invite import Invite

        row = await Invite.find_one({'token_hash': _hash_token(invite)})
        valid_invite = bool(row and not row.used_at and int(row.expires_at or 0) >= int(time.time()))
    return {
        'signup_open': await _signup_open() or valid_invite,
        'password_reset_by_email': mailer.configured(),
    }


class ForgotPassword(BaseModel):
    email: str


class ResetPassword(BaseModel):
    token: str
    new_password: str


@auth_api.post("/forgot")
async def forgot_password(body: ForgotPassword, request: Request):
    """Email a reset link. Always answers the same, so it never reveals
    whether an address has an account."""
    from cognitrix.common import email as mailer

    answer = {"message": "If that address has an account, a reset link is on its way."}
    if not mailer.configured():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is not set up on this server")
    user = await User.find_one({'email': body.email.strip()})
    if user is None or getattr(user, 'disabled', None):
        return answer
    # One link every two minutes per account is plenty.
    expires = int(getattr(user, 'reset_expires', None) or 0)
    if expires - RESET_TOKEN_MINUTES * 60 > time.time() - 120:
        return answer
    token = secrets.token_urlsafe(32)
    await User.update_one({'id': user.id}, {
        'reset_token_hash': _hash_token(token),
        'reset_expires': int(time.time()) + RESET_TOKEN_MINUTES * 60,
    })
    base = mailer.public_url() or str(request.base_url).rstrip('/')
    try:
        await mailer.send(
            user.email,
            'Reset your Cognitrix password',
            f"Hello {user.name},\n\nOpen this link to choose a new password. It works once and "
            f"expires in {RESET_TOKEN_MINUTES} minutes.\n\n{base}/reset-password?token={token}\n\n"
            "If you did not ask for this, you can ignore this email.\n",
        )
    except mailer.EmailError as exc:
        logger.warning('Could not send a password reset email: %s', exc)
    return answer


@auth_api.post("/reset")
async def reset_password(body: ResetPassword):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")
    user = await User.find_one({'reset_token_hash': _hash_token(body.token)}) if body.token else None
    if user is None or int(getattr(user, 'reset_expires', None) or 0) < int(time.time()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This reset link has expired or was already used")
    await User.update_one({'id': user.id}, {
        'password': Utils.hash_password(body.new_password),
        'tokens_valid_after': int(time.time()),
        'reset_token_hash': None,
        'reset_expires': None,
    })
    return {"message": "Password changed. Sign in with the new one."}

class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@auth_api.post("/password")
async def change_password(body: PasswordChange, ctx: Annotated[AuthContext, Depends(jwt_only)]):
    """Change the signed-in person's password. Every other device is signed
    out; this one gets a fresh token back so it stays signed in."""
    # The signed-in user object carries no password hash; read the stored row.
    user = await User.find_one({'email': ctx.user.email})
    if user is None or not verify_password(body.current_password, getattr(user, 'password', '') or ''):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose a password you are not already using")
    await User.update_one({'id': user.id}, {
        'password': Utils.hash_password(body.new_password),
        'tokens_valid_after': int(time.time()),
    })
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=float(JWT_ACCESS_TOKEN_EXPIRE_MINUTES)),
    )
    return {"access_token": access_token, "token_type": "bearer"}


@auth_api.get("/user")
async def get_user(user: Annotated[User, Depends(get_current_user)]):
    from cognitrix.agents.access import is_admin

    data = user.json()
    for secret in ('password', 'reset_token_hash', 'reset_expires'):
        data.pop(secret, None)
    data['is_admin'] = await is_admin(user)
    return data

@auth_api.post("/refresh")
async def refresh(ctx: Annotated[AuthContext, Depends(get_auth_context)]):
    """Swap a valid session token for a fresh one, so an app that's in use
    keeps its sign-in instead of hitting the expiry. API keys can't be
    swapped for a session. Tokens revoked by "Sign out of all devices"
    fail here like anywhere else."""
    if ctx.api_key is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys can't be exchanged for a session token",
        )
    access_token = create_access_token(
        data={"sub": ctx.user.email},
        expires_delta=timedelta(minutes=float(JWT_ACCESS_TOKEN_EXPIRE_MINUTES)),
    )
    return {"access_token": access_token, "token_type": "bearer"}

@auth_api.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """End every session token issued so far for this user (all devices).
    API keys are separate and stay valid until revoked."""
    await User.update_one({'id': current_user.id}, {'tokens_valid_after': int(time.time())})
    return {"message": "Logged out successfully"}
