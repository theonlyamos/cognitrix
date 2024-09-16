import os
import secrets

if not os.getenv("JWT_SECRET_KEY"):
    print("Warning: JWT_SECRET_KEY not set. Generating a new one.")
    print("Please set this in your environment variables for future runs:")
    print(f"JWT_SECRET_KEY={secrets.token_urlsafe(32)}")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 30)