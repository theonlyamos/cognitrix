from cognitrix.config import settings

# JWT configuration - now centralized in settings.
# Secret resolution and the "not set" warning live in CognitrixSettings
# (_resolve_jwt_secret): it persists a dev key across restarts and hard-fails
# in production, replacing the previously-broken always-false check here.
JWT_SECRET_KEY = settings.jwt_secret_key
JWT_ALGORITHM = settings.jwt_algorithm
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = settings.jwt_access_token_expire_minutes
