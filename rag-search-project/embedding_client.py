import os
import logging
from typing import List
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

_openai_client_cache = None


def _get_openai_client():
    global _openai_client_cache
    if _openai_client_cache is None:
        if not OPENAI_API_KEY and not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        from openai import OpenAI
        _openai_client_cache = OpenAI(api_key=OPENAI_API_KEY or None)
    return _openai_client_cache


def create_embeddings(texts: List[str], input_type: str = "passage") -> List[List[float]]:
    del input_type
    client = _get_openai_client()
    resp = client.embeddings.create(
        input=texts,
        model=OPENAI_MODEL,
    )
    return [r.embedding for r in resp.data]


def create_single_embedding(text: str, input_type: str = "query") -> List[float]:
    return create_embeddings([text], input_type=input_type)[0]
