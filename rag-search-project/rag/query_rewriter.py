import os
import json
import logging
from typing import List
import requests

logger = logging.getLogger(__name__)

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "meta/llama-3.3-70b-instruct")

def _call_llm(messages: list, format_json: bool = False):
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.1,
    }
    if format_json:
        payload["response_format"] = {"type": "json_object"}
        
    try:
        resp = requests.post(f"{NVIDIA_BASE_URL}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Query rewrite error: {e}")
        return ""

def decompose_query(query: str) -> List[str]:
    prompt = f"Decompose the following complex query into independent sub-queries for a search engine. Return JSON format with a 'sub_queries' list of strings.\nQuery: {query}"
    messages = [{"role": "user", "content": prompt}]
    res = _call_llm(messages, format_json=True)
    try:
        data = json.loads(res)
        return data.get("sub_queries", [query])
    except:
        return [query]

def expand_query(query: str) -> str:
    prompt = f"Provide search keywords or an expanded version of the query to improve search retrieval. Do not overcomplicate. Output just the expanded terms or phrase.\nQuery: {query}"
    messages = [{"role": "user", "content": prompt}]
    res = _call_llm(messages)
    return res.strip() if res else query

def contextualize_query(query: str, history: List[dict]) -> str:
    if not history:
        return query
    
    # format history
    hist_text = "\\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-4:]])
    prompt = f"Given the conversation history and the latest user query, rewrite the latest query to be a standalone search query. Do NOT answer it, just rewrite it.\n\nHistory:\n{hist_text}\n\nLatest Query: {query}"
    messages = [{"role": "user", "content": prompt}]
    res = _call_llm(messages)
    return res.strip() if res else query
