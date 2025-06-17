import os
from functools import cache

from cognitrix.providers import OpenAI


class MindsDB(OpenAI):
    """A class for interacting with the MindsDB API."""

    model: str ='gemini-1.5-pro'

    api_key: str = os.getenv('MINDS_API_KEY', '')

    base_url: str = 'https://llm.mdb.ai'

    is_multimodal: bool = True
    """Whether the model is multimodal."""

    @staticmethod
    @cache
    def get_supported_models():
        import requests

        api_key = os.environ.get("MINDS_API_KEY")
        url = "https://llm.mdb.ai/models"

        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json"
        }

        response = requests.get(url, headers=headers)

        return response.json()
