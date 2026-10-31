from odbms import Model


class User(Model):
    name: str
    email: str
    password: str
    tokens_valid_after: int | None = None
    """Unix time; session tokens issued before it are rejected (set on logout)."""

