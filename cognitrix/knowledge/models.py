"""Knowledge base records. Collections and documents belong to one person;
the passages themselves live in the vector store (cognitrix/knowledge/store.py)."""
from odbms import Model
from pydantic import field_validator


class KnowledgeCollection(Model):
    user_id: str
    name: str
    description: str = ''
    created: str = ''

    @field_validator('description', 'created', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)


class KnowledgeDocument(Model):
    collection_id: str
    user_id: str
    name: str
    size_bytes: int = 0
    status: str = 'queued'
    """queued, ready or failed."""
    error: str = ''
    passages: int = 0
    characters: int = 0
    created: str = ''

    @field_validator('error', 'created', 'status', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)

    @field_validator('size_bytes', 'passages', 'characters', mode='before')
    @classmethod
    def _number(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
