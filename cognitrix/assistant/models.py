"""Every table the proactive assistant and dream mode add (created at startup in config.py)."""
from cognitrix.assistant.brief import MODELS as BRIEF_MODELS
from cognitrix.assistant.followups import MODELS as FOLLOWUP_MODELS
from cognitrix.assistant.mail import MODELS as MAIL_MODELS
from cognitrix.assistant.proactivity import MODELS as PROACTIVITY_MODELS
from cognitrix.memory.dream import MODELS as DREAM_MODELS
from cognitrix.models.memory import MemoryChange, MemoryProfile

MODELS = (
    *PROACTIVITY_MODELS,
    *FOLLOWUP_MODELS,
    *BRIEF_MODELS,
    *MAIL_MODELS,
    *DREAM_MODELS,
    MemoryChange,
    MemoryProfile,
)
