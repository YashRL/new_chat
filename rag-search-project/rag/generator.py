import os
import json
import logging
from typing import List, Dict, Any, Generator, Optional
import requests
from openai import OpenAI

logger = logging.getLogger(__name__)

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "meta/llama-3.3-70b-instruct")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-mini")
MAX_HISTORY_MESSAGES = int(os.getenv("RAG_HISTORY_MESSAGES", "8"))


def _use_openai_chat() -> bool:
    return bool(OPENAI_API_KEY)


def _generate_with_openai(messages: List[Dict[str, str]]) -> str:
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=messages,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
        max_completion_tokens=int(os.getenv("LLM_MAX_TOKENS", "1024")),
    )
    return (resp.choices[0].message.content or "").strip()

def extract_citations(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    citations = []
    for i, c in enumerate(chunks, 1):
        citations.append({
            "citation_id": i,
            "document_name": c.get("document_name") or c.get("book_title") or "Unknown Document",
            "section_title": c.get("section_title", "Unknown Section"),
            "text": c.get("text", "")
        })
    return citations

def assemble_context(chunks: List[Dict[str, Any]], max_tokens: int = 3000) -> str:
    context_parts = []
    current_tokens = 0
    # Add items until max_tokens reached
    for i, chunk in enumerate(chunks, 1):
        # We roughly estimate 4 chars ~ 1 token for budget purposes if actual count missing
        tok_count = chunk.get("token_count", len(chunk.get("text", ""))/4)
        if current_tokens + tok_count > max_tokens:
            break
        
        doc_name = chunk.get("document_name") or chunk.get("book_title") or "Unknown Document"
        section = chunk.get("section_title", "Unknown Section")
        text = chunk.get("text", "")
        
        part = f"[Citation {i}]\nSource: {doc_name} ({section})\nContent: {text}"
        context_parts.append(part)
        current_tokens += tok_count
        
    return "\n\n".join(context_parts)

def _normalize_history(history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    if not history:
        return []
    normalized = []
    for item in history[-MAX_HISTORY_MESSAGES:]:
        role = str(item.get("role", "user"))
        content = str(item.get("content", "")).strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def build_rag_prompt(
    query: str,
    context: str,
    history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: str = "",
) -> List[Dict[str, str]]:
    sys_instruction = system_prompt or (
        "You are a helpful AI assistant. Answer the user's question based ONLY on the provided retrieved context. "
        "Use the chat history only to resolve references and maintain continuity. "
        "If the answer is not supported by the retrieved context, say so clearly."
    )
    sys_instruction += "\nWhen you use information from the context, always cite it using [Citation X] format at the end of the sentence."

    prompt = (
        "Retrieved context:\n"
        f"{context or 'No relevant context found.'}\n\n"
        "Use the retrieved context as the source of truth.\n"
        f"Current user question: {query}"
    )

    return [
        {"role": "system", "content": sys_instruction},
        *_normalize_history(history),
        {"role": "user", "content": prompt},
    ]


def generate_answer_sync(
    query: str,
    chunks: List[Dict[str, Any]],
    history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: str = "",
) -> Dict[str, Any]:
    context = assemble_context(chunks)
    messages = build_rag_prompt(query, context, history=history, system_prompt=system_prompt)
    citations = extract_citations(chunks)

    try:
        if _use_openai_chat():
            answer = _generate_with_openai(messages)
        else:
            headers = {
                "Authorization": f"Bearer {NVIDIA_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": LLM_MODEL,
                "messages": messages,
                "temperature": float(os.getenv("LLM_TEMPERATURE", "0.1")),
                "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "1024")),
            }
            resp = requests.post(f"{NVIDIA_BASE_URL}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"]
        return {"answer": answer, "citations": citations}
    except Exception as e:
        logger.error(f"Generation error: {e}")
        return {"answer": f"Generation failed: {e}", "citations": []}

def generate_answer_stream(
    query: str,
    chunks: List[Dict[str, Any]],
    history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: str = "",
) -> Generator[str, None, None]:
    context = assemble_context(chunks)
    messages = build_rag_prompt(query, context, history=history, system_prompt=system_prompt)
    
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.1")),
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "1024")),
        "stream": True
    }
    
    try:
        with requests.post(f"{NVIDIA_BASE_URL}/chat/completions", headers=headers, json=payload, stream=True) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:
                    decoded = line.decode('utf-8')
                    if decoded.startswith("data: "):
                        content = decoded[6:]
                        if content == "[DONE]":
                            break
                        try:
                            data = json.loads(content)
                            delta = data["choices"][0]["delta"].get("content", "")
                            if delta:
                                yield delta
                        except json.JSONDecodeError:
                            continue
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        yield "\\n[Error: Unable to complete stream]"
