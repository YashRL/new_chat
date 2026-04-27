# RAG Search System (Agent Harness Edition)

Production-grade RAG backend built for autonomous AI agents. Upgraded with semantic hierarchy chunking, multi-query routing, citation generation, streaming support, and rigorous fairness scoring across large/small documents.

## Key Features

1. **Agent-Ready Generation (`/answer`)**: Full prompt assembly, history contextualization, grounding, generation (via NVIDIA NIM LLMs), and streaming support.
2. **Fairness-Aware Hybrid Search**: Document size fairness corrects biases between massive 500-page manuals and short 2-page briefs.
3. **Robust Data Pipeline (`/ingest`)**: PDF ingestion with OCR fallback, sliding window child chunks, Unicode normalization, ligature expansion, and async job tracking.
4. **Resilient Infrastructure**: SQLite-backed persistence, embedded vector storage, graceful exception handling, and SlowAPI rate limits.

## Setup

1. **Install dependencies**: `pip install -r requirements.txt` (Ensure Python 3.10+)
2. **Environment**: Copy `.env.example` -> `.env` and configure:
   - Provide your `NVIDIA_API_KEY` for embedding + LLM generation
   - Set `SQLITE_PATH` if you want the database file somewhere other than `knowledge.db`
   - Generate a `SECRET_KEY` for JWT auth
3. **Database Setup**: No external database server is required. The app bootstraps its SQLite schema automatically on first run.
4. **Start Server**: `uvicorn api:app --reload --port 8000`

## Agent Harness APIs

- `GET /agent/tools` — Discover available tools dynamically
- `POST /answer` — Complete RAG loop. Send `{ "query": "...", "session_id": "...", "stream": true }`
- `POST /search` — Raw hybrid retrieval. Send `{ "query": "..." }`
- `POST /ingest` — Upload PDF/files to KB

## Testing

Run the isolated fairness test suite:
```bash
python -m pytest tests/test_fairness.py -v
```
Run chunking & generation checks:
```bash
python -m pytest tests/test_ingestion.py -v
python -m pytest tests/test_rag_generation.py -v
```
