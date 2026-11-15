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
    source_url: str = ''
    """Set for a web source: the page it was read from. Files leave it empty."""
    max_pages: int = 1
    """Web sources: 1 reads just the page; more also reads the pages it links
    to under the same folder of the same site, up to this many."""
    pages_read: int = 0
    refresh_days: int = 0
    """Web sources: read the site again every this many days (0 = only when asked)."""
    refreshed: str = ''
    """When the web source was last read, UTC 'YYYY-MM-DD HH:MM:SS'."""

    @field_validator('error', 'created', 'status', 'source_url', 'refreshed', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)

    @field_validator('size_bytes', 'passages', 'characters', 'pages_read', 'refresh_days', mode='before')
    @classmethod
    def _number(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @field_validator('max_pages', mode='before')
    @classmethod
    def _pages(cls, value):
        try:
            return max(1, int(value or 1))
        except (TypeError, ValueError):
            return 1
