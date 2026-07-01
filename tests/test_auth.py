"""Auth tests: bcrypt hashing, PyJWT tokens, and stable JWT secret."""

import bcrypt
import jwt

from cognitrix.common.constants import JWT_ALGORITHM, JWT_SECRET_KEY
from cognitrix.common.security import (
    create_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert verify_password("s3cret-pw", h)
    assert not verify_password("wrong-pw", h)


def test_verifies_existing_bcrypt_hash():
    # Migration safety: hashes created by the old passlib(bcrypt) path use the
    # same $2b$ format, so they must still verify under the new direct-bcrypt code.
    existing = bcrypt.hashpw(b"legacy-pw", bcrypt.gensalt()).decode("utf-8")
    assert verify_password("legacy-pw", existing)


def test_verify_password_handles_garbage():
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_jwt_encode_decode_roundtrip():
    token = create_access_token({"sub": "a@b.com"})
    payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    assert payload["sub"] == "a@b.com"


def test_jwt_rejects_wrong_secret():
    token = create_access_token({"sub": "a@b.com"})
    try:
        jwt.decode(token, "wrong-secret", algorithms=[JWT_ALGORITHM])
        assert False, "decode should have raised on a wrong secret"
    except jwt.PyJWTError:
        pass


def test_jwt_secret_stable_across_instances():
    # The persisted dev key must be reused so tokens survive restarts, unlike the
    # old behaviour that generated a fresh key on every construction.
    from cognitrix.config import CognitrixSettings
    assert CognitrixSettings().jwt_secret_key == CognitrixSettings().jwt_secret_key
