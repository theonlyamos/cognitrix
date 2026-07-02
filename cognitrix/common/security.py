from datetime import datetime, timedelta

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from ..models import User
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
