# api.py
import io
import os
import re
import uuid
import jwt
import gzip
import logging
import zipfile
import datetime as datetime
from typing import Optional
from urllib.parse import unquote_plus
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, UploadFile, File, Query, HTTPException, Form, Request, Body
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import asyncio

# Internal imports
from ingest.ingest import ingest
from search_engine.search import hybrid_search, semantic_search, search_by_book, search_by_section, search_by_keywords, WEIGHT_PROFILES, COMPATIBLE_PROFILES, resolve_weights
from db.db import get_db_connection, run_query, get_db_cursor
from cron.cron_jobs import run_cron
from rag.generator import generate_answer_sync, generate_answer_stream
from rag.query_rewriter import contextualize_query
from chat.chat import get_messages_func, append_message_func
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
from jobs.processor import (
    create_job_id, register_job, update_job_status, get_job_status,
    process_document_async, get_active_jobs
)

# Importing routers
from auth.auth import router as auth_router
from admin.admin import router as admin_router
from user_profile.user_profile import router as profile_router
from assesments.assesments import router as assessments_router
from assesments.direct import router as direct_router
from chat.chat import router as chat_router


# ============================================================================
# CONFIGURATION
# ============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploaded_pdfs")
os.makedirs(UPLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"


# ============================================================================
# FASTAPI APP SETUP
# ============================================================================
app = FastAPI(
    title="Content Retrieval API",
    description="Backend API for content retrieval and search.",
    version="3.0.0",
    swagger_ui_parameters={"persistAuthorization": True},
)
app.state.limiter = limiter
app.add_exception_handler(429, _rate_limit_exceeded_handler)

# Raise the per-part size limit so PDFs larger than Starlette's
# default 1 MB can be uploaded. Set to 500 MB.
from starlette.formparsers import MultiPartParser
MultiPartParser.max_part_size = 500 * 1024 * 1024


def _resolve_authenticated_user_id(request: Request) -> Optional[str]:
    user_id = getattr(request.state, "user_id", None)
    username = getattr(request.state, "username", None)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            if user_id:
                cur.execute("SELECT id FROM users WHERE id = %s", (str(user_id),))
                row = cur.fetchone()
                if row:
                    return row[0]
                logger.warning("Token user_id not found in DB, falling back to username lookup: %s", user_id)

            if username:
                cur.execute("SELECT id FROM users WHERE email = %s", (username,))
                row = cur.fetchone()
                if row:
                    return row[0]

    return None

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    if AUTH_ENABLED:
        schema["components"]["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }
        for path in schema["paths"].values():
            for method in path.values():
                method["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return schema

app.openapi = custom_openapi

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Make sure this is BEFORE any route definitions
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# ============================================================================
# ROUTER REGISTRATION
# ============================================================================
app.include_router(chat_router)
if AUTH_ENABLED:
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(assessments_router)
    app.include_router(direct_router)
    app.include_router(profile_router)


@app.get("/")
async def serve_frontend():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


# ============================================================================
# STARTUP & SHUTDOWN EVENTS
# ============================================================================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_cron())
    print("🚀 Backend starting up...")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 Backend shutting down...")


# ============================================================================
# Auth Middleware
# ============================================================================

SECRET_KEY = os.getenv("SECRET_KEY", "your_super_secret_key")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """JWT-based authentication middleware with better logging."""
    if not AUTH_ENABLED:
        request.state.username = "guest@system.local"
        request.state.user_id = "user-guest"
        request.state.role = "Guest"
        request.state.display_name = "Anonymous"
        return await call_next(request)
    
    # Public paths that don't need authentication
    public_paths = ("/auth", "/health", "/openapi.json", "/docs", "/redoc", "/static", "/favicon.ico")
    
    # Allow CORS preflight and public endpoints
    if request.method == "OPTIONS":
        return await call_next(request)
    
    # Check if path is public (exact match only, no prefix matching)
    is_public = any(request.url.path == p or request.url.path.startswith(p + "/") for p in public_paths)
    
    # GET /books is public for unauthenticated users (shows only public docs),
    # but if a Bearer token is present we decode it so private docs are visible
    # to their owner. We never reject the request — worst case user_id stays None.
    if request.url.path.startswith("/books"):
        if request.method == "GET":
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1]
                try:
                    payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
                    request.state.username = payload.get("username")
                    request.state.user_id = payload.get("user_id")
                    request.state.role = payload.get("role", "User")
                    request.state.display_name = payload.get("display_name", "")
                except Exception:
                    pass
            return await call_next(request)
        # DELETE, POST, etc. require full auth — fall through
    elif is_public:
        return await call_next(request)

    # --- Authentication Required Beyond This Point ---
    auth_header = request.headers.get("Authorization")
    
    if not auth_header:
        logger.warning(f"No Authorization header for {request.method} {request.url.path}")
        return JSONResponse(status_code=401, content={"detail": "Missing authorization header"})
    
    if not auth_header.startswith("Bearer "):
        logger.warning(f"Invalid Authorization format for {request.method} {request.url.path}")
        return JSONResponse(status_code=401, content={"detail": "Invalid authorization format"})

    token = auth_header.split(" ", 1)[1]
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])

        username = payload.get("username")
        if not username:
            return JSONResponse(status_code=401, content={"detail": "Invalid token payload"})

        request.state.username = username
        request.state.user_id = payload.get("user_id")
        request.state.role = payload.get("role", "User")
        request.state.display_name = payload.get("display_name", username)

        request.state.user_id = _resolve_authenticated_user_id(request)
        if not request.state.user_id:
            return JSONResponse(status_code=401, content={"detail": "User not found"})

        logger.info(f"Auth: {username} ({request.state.role}) → {request.method} {request.url.path}")

    except jwt.ExpiredSignatureError:
        logger.warning(f"Expired token for {request.method} {request.url.path}")
        return JSONResponse(status_code=401, content={"detail": "Token expired"})
    
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token for {request.method} {request.url.path}: {e}")
        return JSONResponse(status_code=401, content={"detail": "Invalid token"})
    
    except Exception as e:
        logger.error(f"Auth middleware error for {request.method} {request.url.path}: {e}")
        return JSONResponse(status_code=500, content={"detail": f"Authentication error: {str(e)}"})

    return await call_next(request)


# ============================================================================
# PDF INGESTION (synchronous - waits for ingestion to complete)
# ============================================================================
@app.post("/ingest")
async def ingest_pdf(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(...),
    doc_type: str = Form(...),
    keywords: str = Form("", description="Comma-separated keywords"),
    visibility: str = Form("everyone"),
    mode: str = Form("sync", description="sync or async mode")
):
    MAX_FILE_SIZE = 500 * 1024 * 1024
    ALLOWED_TYPES = {"application/pdf", "application/octet-stream"}

    if file.content_type not in ALLOWED_TYPES and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    safe_name = re.sub(r"[^\w\-.]", "_", os.path.basename(file.filename or "upload.pdf"))
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)
    keyword_list = [k.strip() for k in keywords.split(",")] if keywords else []

    current_user_email = getattr(request.state, "username", None) or "guest@system.local"
    current_user = _resolve_authenticated_user_id(request)

    visibility_json = {"everyone": True} if visibility == "everyone" else {"private": True}

    try:
        size = 0
        with open(file_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    buffer.close()
                    os.remove(file_path)
                    raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {MAX_FILE_SIZE // 1024 // 1024}MB")
                buffer.write(chunk)

        if mode == "async":
            job_id = create_job_id()
            register_job(job_id, "ingestion", {
                "filename": safe_name,
                "title": title,
                "file_path": file_path,
            })
            
            asyncio.create_task(
                process_document_async(
                    job_id=job_id,
                    file_path=file_path,
                    title=title,
                    doc_type=doc_type,
                    keywords=keyword_list,
                    created_by=current_user,
                    visibility=visibility_json,
                )
            )
            
            return {
                "status": "queued",
                "message": "Ingestion job has been queued for processing.",
                "job_id": job_id,
                "document_name": safe_name,
                "estimated_time": "30-90 seconds for large PDFs",
            }
        else:
            document_id = ingest(
                file_path=file_path,
                keywords=keyword_list,
                doc_type=doc_type,
                override_title=title.strip(),
                created_by=current_user,
                updated_by=current_user,
                visibility=visibility_json,
            )

            return {
                "status": "accepted",
                "message": f"Ingestion completed for '{safe_name}'.",
                "document_id": str(document_id),
                "visibility": visibility_json,
                "keywords": keyword_list,
                "filename": safe_name,
                "initiated_by": current_user_email,
            }

    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")


@app.get("/ingest/status/{job_id}")
async def get_ingest_status(job_id: str):
    status = get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return status


@app.get("/ingest/jobs")
async def list_active_jobs():
    jobs = get_active_jobs()
    return {"jobs": jobs, "count": len(jobs)}


# ============================================================================
# BOOK MANAGEMENT
# ============================================================================
def human_readable_size(size_bytes):
    if not size_bytes:
        return None
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"

@app.get("/books")
async def list_books(request: Request):
    """
    Return:
      - public docs (visibility->>'everyone' = 'true')
      - documents created by the current user (even if visibility is restricted)
    """

    # ============================================================
    # 1. IDENTIFY USER FROM TOKEN
    # ============================================================
    current_user_email = getattr(request.state, "username", None) or "guest@system.local"
    
    user_row = run_query("SELECT id FROM users WHERE email=%s", (current_user_email,))
    user_id = user_row[0]["id"] if user_row else None

    # ============================================================
    # 2. QUERY DOCUMENTS (public or owned by user)
    # ============================================================
    query = """
        SELECT
            d.id,
            d.document_name AS title,
            d.document_type,
            d.keywords,
            d.total_tokens,
            d.created_by,
            d.created_at,
            d.updated_at,
            COALESCE(creator.email, 'Guest User') AS created_by_name,
            COALESCE(updater.email, creator.email, 'Guest User') AS updated_by_name,
            d.visibility,
            d.language,
            d.mime_type,
            COUNT(DISTINCT p.id) AS paragraph_count,
            df.filename AS file_name,
            df.expiry_date,
            df.mime_type AS file_mime_type,
            LENGTH(df.file_data) AS file_size_bytes
        FROM documents d
        LEFT JOIN users creator ON d.created_by = creator.id
        LEFT JOIN users updater ON d.updated_by = updater.id
        LEFT JOIN paragraphs p ON p.document_id = d.id
        LEFT JOIN document_files df ON df.document_id = d.id
        GROUP BY
            d.id, d.document_name, d.document_type, d.keywords, d.total_tokens, d.created_by,
            d.created_at, d.updated_at, creator.email, updater.email, d.visibility, d.language,
            d.mime_type, df.filename, df.expiry_date, df.mime_type, df.file_data
        ORDER BY d.updated_at DESC
    """

    raw_result = run_query(query)
    result = []
    for row in raw_result:
        visibility_data = row.get("visibility") or {}
        is_public = bool(visibility_data.get("everyone"))
        is_owner = str(row.get("created_by")) == str(user_id) if user_id else False
        if is_public or is_owner:
            result.append(row)

    # ============================================================
    # 3. TRANSFORM RESULT
    # ============================================================
    now = datetime.datetime.now(datetime.timezone.utc)

    for row in result:

        # Convert datetimes
        for k, v in row.items():
            if isinstance(v, (datetime.datetime, datetime.date)):
                row[k] = v.isoformat()

        # File size pretty format
        size_bytes = row.get("file_size_bytes")
        row["file_size_pretty"] = human_readable_size(size_bytes)

        # Expiry logic
        expiry_str = row.get("expiry_date")
        if expiry_str:
            expiry_dt = expiry_str if isinstance(expiry_str, datetime.datetime) else datetime.datetime.fromisoformat(expiry_str)
            row["is_expired"] = expiry_dt < now
            delta = expiry_dt - now
            row["days_left"] = f"{max(delta.days, 0)} days"
        else:
            row["is_expired"] = None
            row["days_left"] = None

    return JSONResponse(content=result)


@app.get("/books/{book_id}")
async def get_book_by_id(
    book_id: str,
    max_words: int = 1024,
    limit_paragraphs: int = 10,
    max_total_words: int = 5000
):
    # 1. Fetch the document metadata by ID
    doc_query = """
        SELECT 
            d.id,
            d.document_name AS title,
            COALESCE(u.email, 'Guest User') AS created_by_email,
            d.visibility
        FROM documents d
        LEFT JOIN users u ON d.created_by = u.id
        WHERE d.id = %s
        LIMIT 1;
    """

    doc = run_query(doc_query, (book_id,))
    if not doc:
        return JSONResponse(content={"error": "Document not found"}, status_code=404)

    doc_id = doc[0]["id"]

    # 2. Fetch paragraphs with strong type-casting for LIMIT
    para_query = """
        SELECT id, text, paragraph_index
        FROM paragraphs
        WHERE document_id = %s
        ORDER BY paragraph_index
        LIMIT CAST(%s AS INTEGER);
    """

    raw_paragraphs = run_query(para_query, (doc_id, limit_paragraphs))

    # Python fallback if DB LIMIT fails
    raw_paragraphs = raw_paragraphs[:limit_paragraphs]

    # Helper to cut text by word limit
    def truncate_words(text: str, limit: int) -> str:
        words = text.split()
        if len(words) <= limit:
            return text
        return " ".join(words[:limit]) + " ..."

    # 3. Apply word limits
    total_words_used = 0
    filtered_paragraphs = []

    for p in raw_paragraphs:
        # Stop if global limit reached
        if total_words_used >= max_total_words:
            break

        remaining_budget = max_total_words - total_words_used

        # First cut each paragraph individually
        truncated = truncate_words(p["text"], max_words)
        words = truncated.split()

        # Then cut again if total budget is exceeded
        if len(words) > remaining_budget:
            words = words[:remaining_budget]
            truncated = " ".join(words) + " ..."

        total_words_used += len(words)

        filtered_paragraphs.append({
            "id": p["id"],
            "text": truncated,
            "paragraph_index": p["paragraph_index"],
        })

        if total_words_used >= max_total_words:
            break

    # 4. Return final truncated document
    doc[0]["paragraphs"] = filtered_paragraphs
    return JSONResponse(content=doc[0])


@app.delete("/books/{book_id}")
async def remove_book(book_id: str, request: Request):
    """
    Delete a book. Admin-only endpoint (no permission checks).
    """
    # 1. Get current user from middleware (should already be set)
    current_user_email = getattr(request.state, "username", None)
    user_id = getattr(request.state, "user_id", None)
    
    # Safety check - should never happen if middleware works
    if not current_user_email or not user_id:
        logger.error(f"Auth middleware failed - username={current_user_email}, user_id={user_id}")
        raise HTTPException(status_code=401, detail="Authentication required.")

    # 2. Verify document exists and fetch its owner
    doc = run_query("SELECT id, created_by FROM documents WHERE id=%s", (book_id,))
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    owner_id = str(doc[0]["created_by"]) if doc[0]["created_by"] else None
    requester_id = str(user_id) if user_id else None
    is_admin = getattr(request.state, "role", "User") == "Admin"

    if AUTH_ENABLED and not is_admin and owner_id != requester_id:
        logger.warning(
            f"Forbidden delete attempt: user={current_user_email} (id={requester_id}) "
            f"tried to delete doc={book_id} owned by id={owner_id}"
        )
        raise HTTPException(status_code=403, detail="You can only delete documents you uploaded.")
    
    logger.info(f"Delete request: user={current_user_email} (id={user_id}), doc={book_id}")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, metadata FROM chat_sessions")
                detached_sessions = []
                for session in cur.fetchall():
                    metadata = session.get("metadata") or {}
                    uploaded_docs = metadata.get("uploaded_docs") or []
                    filtered_docs = [doc for doc in uploaded_docs if str(doc.get("doc_id")) != str(book_id)]
                    if len(filtered_docs) != len(uploaded_docs):
                        metadata["uploaded_docs"] = filtered_docs
                        cur.execute(
                            "UPDATE chat_sessions SET metadata = %s WHERE id = %s",
                            (metadata, session["id"]),
                        )
                        detached_sessions.append(session["id"])

                cur.execute("DELETE FROM documents WHERE id=%s;", (book_id,))
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Document not found during deletion.")

                conn.commit()

        logger.info(f"Document {book_id} deleted successfully by {current_user_email}")
        return {
            "message": f"Document deleted successfully.",
            "document_id": book_id,
            "detached_from_sessions": detached_sessions,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting document {book_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")


# ============================================================================
# STREAMING SEARCH (Agent-friendly)
# ============================================================================
@app.post("/search/stream")
async def stream_search(
    request: Request,
    query: str = Query(..., description="Search query"),
    search_type: str = Query("hybrid", description="Search type"),
    weight_profile: str = Query("auto", description="Weight profile"),
    book: str = Query(None),
    section: str = Query(None),
    keywords: str = Query(None),
    limit: int = Query(10, ge=1, le=100),
    use_hyde: bool = Query(False),
    use_rerank: bool = Query(False),
):
    import json
    from datetime import datetime
    
    async def generate():
        try:
            yield f"data: {json.dumps({'event': 'start', 'timestamp': datetime.utcnow().isoformat()})}\n\n"
            
            decoded_query = unquote_plus(query) if query else None
            filters = {}
            if book:
                filters['book'] = book
            if section:
                filters['section'] = section
            if keywords:
                filters['keywords'] = keywords
                
            effective_search_type = search_type or "hybrid"
            weights = resolve_weights(effective_search_type, weight_profile or "auto", decoded_query)
            
            yield f"data: {json.dumps({'event': 'searching', 'query': decoded_query, 'weights': weights})}\n\n"
            
            results = hybrid_search(
                query=decoded_query, top_k=limit,
                filters=filters, weights=weights,
                search_type=search_type,
                use_hyde=use_hyde, use_rerank=use_rerank,
            )
            
            yield f"data: {json.dumps({'event': 'results_count', 'count': len(results)})}\n\n"
            
            for i, result in enumerate(results):
                yield f"data: {json.dumps({'event': 'result', 'index': i, 'data': result})}\n\n"
            
            yield f"data: {json.dumps({'event': 'complete', 'timestamp': datetime.utcnow().isoformat()})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")


# ============================================================================
# SEARCH
# ============================================================================
@app.get("/search")
def search(
    # Core search params
    query: Optional[str] = Query(None, description="Search query text"),
    search_type: Optional[str] = Query(
        None, 
        enum=["semantic", "book", "section", "keywords", "hybrid"]
    ),
    limit: int = Query(10, ge=1, le=100),
    
    # Filter params
    book: Optional[str] = Query(None, description="Book title or ID"),
    book_id: Optional[str] = Query(None, description="Exact book UUID"),
    section: Optional[str] = Query(None, description="Section title"),
    section_id: Optional[str] = Query(None, description="Exact section UUID"),
    keywords: Optional[str] = Query(None, description="Comma-separated keywords"),
    
    # User/date filters
    uploader_email: Optional[str] = None,
    uploader_id: Optional[str] = None,
    created_after: Optional[str] = Query(None, description="YYYY-MM-DD"),
    created_before: Optional[str] = Query(None, description="YYYY-MM-DD"),
    
    # Content filters
    min_tokens: Optional[int] = Query(None, ge=0),
    max_tokens: Optional[int] = Query(None, ge=0),
    
    # Scoring params
    weight_profile: Optional[str] = Query(
        "auto",
        enum=["auto", "balanced", "semantic", "semantic-heavy", "lexical", "lexical-heavy", "precise"],
        description="Preset scoring profile. 'auto' selects based on query type."
    ),
    semantic_weight: Optional[float] = Query(None, ge=0, le=1),
    lexical_weight: Optional[float] = Query(None, ge=0, le=1),
    keyword_weight: Optional[float] = Query(None, ge=0, le=1),
    context_weight: Optional[float] = Query(None, ge=0, le=1),
    
    # Advanced options
    candidate_pool: int = Query(200, ge=10, le=1000),
    enable_context_boost: bool = Query(True),
    dedup_strategy: str = Query("hash", enum=["hash", "cluster", "none"]),
    use_hyde: bool = Query(False, description="Use HyDE (Hypothetical Document Embeddings) for better recall"),
    use_rerank: bool = Query(False, description="Use cross-encoder reranking for better precision"),
):
    """
    🔍 Advanced Hybrid Search API
    
    Combines semantic (vector), lexical (full-text), and keyword search
    with intelligent query analysis and contextual ranking.
    
    **Search Types:**
    - `hybrid` (default): Combines all signals intelligently
    - `semantic`: Prioritize semantic similarity
    - `book`: Search within a specific book
    - `section`: Search within a section
    - `keywords`: Match by document keywords only
    
    **Weight Profiles:**
    - `balanced`: Equal emphasis (default)
    - `semantic`: For conceptual queries
    - `lexical`: For exact term matching
    - `precise`: For technical/definition queries
    
    **Examples:**
    ```
    # Semantic search
    GET /search?query=machine%20learning&search_type=semantic
    
    # Search in specific book
    GET /search?query=optimization&book=Deep%20Learning&limit=20
    
    # Filter by keywords and date
    GET /search?keywords=ai,ml&created_after=2024-01-01
    
    # Custom weights
    GET /search?query=gradient%20descent&semantic_weight=0.7&lexical_weight=0.3
    ```
    """
    try:
        # Decode query
        decoded_query = unquote_plus(query) if query else None
        
        # Build filters dict
        filters = {}
        if book:
            filters['book'] = book
        if book_id:
            filters['book_id'] = book_id
        if section:
            filters['section'] = section
        if section_id:
            filters['section_id'] = section_id
        if keywords:
            filters['keywords'] = keywords
        if uploader_email:
            filters['uploader_email'] = uploader_email
        if uploader_id:
            filters['uploader_id'] = uploader_id
        if created_after:
            filters['created_after'] = created_after
        if created_before:
            filters['created_before'] = created_before
        if min_tokens is not None:
            filters['min_tokens'] = min_tokens
        if max_tokens is not None:
            filters['max_tokens'] = max_tokens
        
        # Build weights — resolve_weights handles auto, aliases, and compatibility
        effective_search_type = search_type or "hybrid"
        if any([semantic_weight, lexical_weight, keyword_weight, context_weight]):
            weights = {
                "semantic": semantic_weight or 0.5,
                "lexical":  lexical_weight  or 0.3,
                "keywords": keyword_weight  or 0.1,
                "context":  context_weight  or 0.1,
            }
            total = sum(weights.values())
            if total > 0:
                weights = {k: v / total for k, v in weights.items()}
        else:
            weights = resolve_weights(effective_search_type, weight_profile or "auto", decoded_query)

        # Route to appropriate search function
        if search_type == "semantic":
            results = hybrid_search(
                query=decoded_query, top_k=limit,
                filters=filters, weights=weights,
                search_type="semantic",
                candidate_pool=candidate_pool,
                enable_context_boost=enable_context_boost,
                dedup_strategy=dedup_strategy,
                use_hyde=use_hyde, use_rerank=use_rerank,
            )
        elif search_type == "book":
            results = hybrid_search(
                query=decoded_query, top_k=limit,
                filters={**filters, **({"book": book} if book else {}), **({"book_id": book_id} if book_id else {})},
                weights=weights, search_type="book",
                candidate_pool=candidate_pool,
                enable_context_boost=enable_context_boost,
                dedup_strategy=dedup_strategy,
                use_hyde=use_hyde, use_rerank=use_rerank,
            )
        elif search_type == "section":
            results = hybrid_search(
                query=decoded_query, top_k=limit,
                filters={**filters, **({"section": section} if section else {}), **({"section_id": section_id} if section_id else {})},
                weights=weights, search_type="section",
                candidate_pool=candidate_pool,
                enable_context_boost=enable_context_boost,
                dedup_strategy=dedup_strategy,
                use_hyde=use_hyde, use_rerank=use_rerank,
            )
        elif search_type == "keywords":
            results = hybrid_search(
                query=decoded_query, top_k=limit,
                filters={**filters, **({"keywords": keywords} if keywords else {})},
                weights=weights, search_type="keywords",
                candidate_pool=candidate_pool,
                enable_context_boost=enable_context_boost,
                dedup_strategy=dedup_strategy,
            )
        else:
            results = hybrid_search(
                query=decoded_query, top_k=limit,
                filters=filters, weights=weights,
                search_type="hybrid",
                candidate_pool=candidate_pool,
                enable_context_boost=enable_context_boost,
                dedup_strategy=dedup_strategy,
                use_hyde=use_hyde, use_rerank=use_rerank,
            )
        
        # Build response with metadata
        response = {
            "results": results,
            "metadata": {
                "query": decoded_query,
                "count": len(results),
                "filters_applied": filters,
                "weights_used": weights or "auto-selected",
                "search_type": search_type or "hybrid",
                "dedup_strategy": dedup_strategy
            }
        }
        
        if not results:
            response["message"] = "No results found. Try adjusting filters or search terms."
        
        return response
        
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=f"Invalid parameters: {ve}")
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.get("/search/suggest")
def search_suggestions(
    query: str = Query(..., min_length=2),
    limit: int = Query(5, ge=1, le=20)
):
    """
    Get search suggestions based on partial query.
    Returns matching book titles, section titles, and keywords.
    """
    try:
        query_pattern = f"%{query.lower()}%"
        suggestions = {"books": [], "sections": [], "keywords": []}
        
        with get_db_cursor() as cur:
            # Book suggestions
            cur.execute("""
                SELECT DISTINCT document_name 
                FROM documents 
                WHERE LOWER(document_name) LIKE %s 
                LIMIT %s;
            """, (query_pattern, limit))
            suggestions["books"] = [r['document_name'] for r in cur.fetchall()]
            
            # Section suggestions
            cur.execute("""
                SELECT DISTINCT title 
                FROM sections 
                WHERE LOWER(title) LIKE %s 
                LIMIT %s;
            """, (query_pattern, limit))
            suggestions["sections"] = [r['title'] for r in cur.fetchall()]

            cur.execute("SELECT keywords FROM documents")
            keyword_matches = []
            for row in cur.fetchall():
                for keyword in row.get("keywords") or []:
                    if query.lower() in str(keyword).lower() and keyword not in keyword_matches:
                        keyword_matches.append(keyword)
                        if len(keyword_matches) >= limit:
                            break
                if len(keyword_matches) >= limit:
                    break
            suggestions["keywords"] = keyword_matches
        
        return suggestions
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# AGENT TOOL MANIFEST & RAG ANSWER LAYER
# ============================================================================
from pydantic import BaseModel
from typing import Optional, Any, Dict
import json

@app.get("/agent/tools")
async def get_agent_tools():
    """Returns a machine-readable JSON manifest of all available tools that an external AI agent can call via HTTP."""
    return {
        "tools": [
            {
                "name": "search_knowledge_base",
                "description": "Search the knowledge base for semantic and keyword matches.",
                "endpoint": "POST /search",
                "parameters": {"query": "string", "limit": "integer"}
            },
            {
                "name": "generate_answer",
                "description": "Retrieve context and generate an AI answer with citations.",
                "endpoint": "POST /answer",
                "parameters": {"query": "string", "history": "array", "stream": "boolean"}
            }
        ]
    }

class AnswerRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    stream: bool = False
    filters: Optional[dict] = None
    history: Optional[list[dict]] = None

@app.post("/answer")
@limiter.limit(os.getenv("RATE_LIMIT_ANSWER", "30/minute"))
async def generate_rag_answer(request: Request, req: AnswerRequest):
    user_query = req.query.strip()
    history = []

    if req.session_id:
        session_messages = get_messages_func(req.session_id, limit=5)
        history.extend(
            {"role": m["role"], "content": m["content"]}
            for m in session_messages
            if m.get("content")
        )

    if req.history:
        history.extend(
            {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
            for m in req.history
            if m and m.get("content")
        )

    search_query = user_query
    if history:
        search_query = contextualize_query(user_query, history)
        
    try:
        results = hybrid_search(
            query=search_query,
            top_k=int(os.getenv("RAG_TOP_K", "8")),
            filters=req.filters,
        )
    except Exception as e:
        logger.error(f"Search failed during answer generation: {e}")
        raise HTTPException(status_code=500, detail="Retrieval failed")
        
    if req.stream:
        if req.session_id:
            append_message_func(req.session_id, {"role": "user", "content": user_query})

        async def generator():
            from rag.generator import generate_answer_stream
            buffer = ""
            for chunk in generate_answer_stream(user_query, results, history=history):
                buffer += chunk
                yield f"data: {json.dumps({'content': chunk})}\n\n"
            if req.session_id:
                append_message_func(req.session_id, {"role": "assistant", "content": buffer})
                
        return StreamingResponse(generator(), media_type="text/event-stream")
    else:
        from rag.generator import generate_answer_sync
        answer = generate_answer_sync(user_query, results, history=history)
        answer["search_query"] = search_query
        answer["retrieved_count"] = len(results)
        if req.session_id:
            append_message_func(req.session_id, {"role": "user", "content": user_query})
            append_message_func(req.session_id, {"role": "assistant", "content": answer["answer"]})
        return answer

# ============================================================================
# HEALTH
# ============================================================================
@app.get("/health")
async def health_check():
    # Check DB
    try:
        from db.db import run_query
        run_query("SELECT 1;")
    except Exception as e:
        logger.error(f"Healthcheck failed (DB): {e}")
        raise HTTPException(status_code=503, detail="Database unhealthy")
        
    return {
        "status": "healthy",
        "database": "connected"
    }


# ============================================================================
# Download file endpoint
# ============================================================================
@app.get("/documents/{document_id}/download")
async def download_document_pdf(document_id: str):
    
    query = """
        SELECT 
            df.file_data,
            df.filename,
            df.mime_type,
            df.expiry_date
        FROM document_files df
        WHERE df.document_id = %s
        LIMIT 1;
    """

    rows = run_query(query, (document_id,))

    if not rows:
        raise HTTPException(404, "No stored PDF found. Either this is a legacy upload or file is missing.")

    compressed_bytes = rows[0]["file_data"]
    filename = rows[0]["filename"] or "document.pdf"
    expiry_date = rows[0]["expiry_date"]

    # Expiry check
    if expiry_date:
        now = datetime.datetime.now(datetime.timezone.utc)
        if expiry_date < now:
            raise HTTPException(410, "The stored PDF has expired. Please re-upload the document.")
    try:
        pdf_bytes = gzip.decompress(compressed_bytes)
    except Exception:
        raise HTTPException(
            500, 
            "Stored PDF is invalid or not GZIP compressed as expected."
        )

    # Create ZIP in-memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr(filename, pdf_bytes)

    zip_buffer.seek(0)

    zip_filename = filename.replace(".pdf", "") + ".zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename=\"{zip_filename}\"'
        }
    )


@app.patch("/documents/{document_id}/expiry")
async def update_document_expiry(
    document_id: str, 
    expiry_date: str = Body(..., embed=True)
):
    """
    Update the expiry date of a stored PDF.
    Adds clear errors for missing file and bad date formats.
    """

    # Validate date format
    try:
        new_expiry = datetime.datetime.fromisoformat(expiry_date.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid expiry_date format. Use ISO8601 (e.g. 2025-12-20T10:00:00Z)"
        )

    # Check file exists
    rows = run_query("""
        SELECT document_id, expiry_date
        FROM document_files 
        WHERE document_id = %s
        LIMIT 1;
    """, (document_id,))

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Cannot update expiry. Either the document was uploaded before file-storage support (legacy), or it has no stored PDF."
        )

    # Update expiry date
    run_query("""
        UPDATE document_files
        SET expiry_date = %s, updated_at = NOW()
        WHERE document_id = %s
    """, (new_expiry, document_id))

    return {
        "document_id": document_id,
        "new_expiry_date": new_expiry.isoformat(),
        "message": "Expiry date updated successfully"
    }
