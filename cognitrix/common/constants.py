from cognitrix.config import settings

# JWT configuration - now centralized in settings
JWT_SECRET_KEY = settings.jwt_secret_key
JWT_ALGORITHM = settings.jwt_algorithm
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = settings.jwt_access_token_expire_minutes

# Show warning if JWT secret key is auto-generated
if not settings.jwt_secret_key or settings.jwt_secret_key == settings.__class__().jwt_secret_key:
    print("Warning: JWT_SECRET_KEY not set. Using auto-generated key.")
    print("Please set this in your environment variables for production:")
    print(f"JWT_SECRET_KEY={settings.jwt_secret_key}")
