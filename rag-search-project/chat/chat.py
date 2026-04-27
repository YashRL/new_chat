import os
import logging
from typing import Optional
import datetime as datetime
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db.db import get_db_connection, run_query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


class CreateSessionRequest(BaseModel):
    title: Optional[str] = "New Chat"
    model: Optional[str] = "gpt-4.1"


class RenameSessionRequest(BaseModel):
    title: str


class SendMessageRequest(BaseModel):
    content: str
    sender: Optional[str] = "user"


def _require_user(request: Request):
    user_id = getattr(request.state, "user_id", None)
    username = getattr(request.state, "username", None)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            if user_id:
                cur.execute("SELECT id FROM users WHERE id = %s LIMIT 1", (str(user_id),))
                row = cur.fetchone()
                if row:
                    request.state.user_id = row[0]
                    return str(row[0])
                logger.warning("Chat auth received unknown user_id=%s, falling back to username lookup", user_id)

            if username:
                cur.execute("SELECT id FROM users WHERE email = %s LIMIT 1", (username,))
                row = cur.fetchone()
                if row:
                    request.state.user_id = row[0]
                    return str(row[0])

    raise HTTPException(status_code=401, detail="Authentication required.")


def _iso_or_none(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value).isoformat()
        except ValueError:
            return value
    return str(value)


# ============================================================================
# SESSIONS
# ============================================================================

@router.post("/sessions")
async def create_session(body: CreateSessionRequest, request: Request):
    user_id = _require_user(request)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO chat_sessions (user_id, title, model)
                    VALUES (%s, %s, %s)
                    RETURNING id, title, model, created_at, updated_at, message_count, last_activity, is_archived
                """, (user_id, body.title, body.model))
                row = cur.fetchone()
                conn.commit()
        return {
            "id": str(row[0]),
            "title": row[1],
            "model": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
            "updated_at": row[4].isoformat() if row[4] else None,
            "message_count": row[5],
            "last_activity": row[6].isoformat() if row[6] else None,
            "is_archived": row[7],
        }
    except Exception as e:
        logger.error(f"Create session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions")
async def list_sessions(
    request: Request,
    include_archived: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    user_id = _require_user(request)
    try:
        archived_filter = "" if include_archived else "AND is_archived = FALSE"
        rows = run_query(f"""
            SELECT id, title, model, created_at, updated_at,
                   message_count, last_activity, is_archived, metadata
            FROM chat_sessions
            WHERE user_id = %s {archived_filter}
            ORDER BY last_activity DESC NULLS LAST, updated_at DESC
            LIMIT %s OFFSET %s
        """, (user_id, limit, offset))

        sessions = []
        for r in rows:
            sessions.append({
                "id": str(r["id"]),
                "title": r["title"],
                "model": r["model"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                "message_count": r["message_count"],
                "last_activity": r["last_activity"].isoformat() if r["last_activity"] else None,
                "is_archived": r["is_archived"],
            })
        return {"sessions": sessions, "count": len(sessions), "offset": offset}
    except Exception as e:
        logger.error(f"List sessions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    user_id = _require_user(request)
    rows = run_query("""
        SELECT id, title, model, created_at, updated_at,
               message_count, last_activity, is_archived, metadata
        FROM chat_sessions
        WHERE id = %s AND user_id = %s
        LIMIT 1
    """, (session_id, user_id))

    if not rows:
        raise HTTPException(status_code=404, detail="Session not found.")

    r = rows[0]
    return {
        "id": str(r["id"]),
        "title": r["title"],
        "model": r["model"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        "message_count": r["message_count"],
        "last_activity": r["last_activity"].isoformat() if r["last_activity"] else None,
        "is_archived": r["is_archived"],
        "metadata": r["metadata"],
    }


@router.patch("/sessions/{session_id}/rename")
async def rename_session(session_id: str, body: RenameSessionRequest, request: Request):
    user_id = _require_user(request)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE chat_sessions SET title = %s
                    WHERE id = %s AND user_id = %s
                    RETURNING id
                """, (body.title, session_id, user_id))
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Session not found.")
                conn.commit()
        return {"message": "Session renamed.", "session_id": session_id, "title": body.title}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Rename session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/sessions/{session_id}/archive")
async def archive_session(session_id: str, request: Request):
    user_id = _require_user(request)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE chat_sessions SET is_archived = TRUE
                    WHERE id = %s AND user_id = %s
                    RETURNING id
                """, (session_id, user_id))
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Session not found.")
                conn.commit()
        return {"message": "Session archived.", "session_id": session_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Archive session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/sessions/{session_id}/unarchive")
async def unarchive_session(session_id: str, request: Request):
    user_id = _require_user(request)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE chat_sessions SET is_archived = FALSE
                    WHERE id = %s AND user_id = %s
                    RETURNING id
                """, (session_id, user_id))
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Session not found.")
                conn.commit()
        return {"message": "Session unarchived.", "session_id": session_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unarchive session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    user_id = _require_user(request)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM chat_sessions
                    WHERE id = %s AND user_id = %s
                    RETURNING id
                """, (session_id, user_id))
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Session not found.")
                conn.commit()
        return {"message": "Session deleted.", "session_id": session_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# MESSAGES
# ============================================================================

@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_deleted: bool = Query(False),
):
    user_id = _require_user(request)

    session_check = run_query(
        "SELECT id FROM chat_sessions WHERE id = %s AND user_id = %s LIMIT 1",
        (session_id, user_id)
    )
    if not session_check:
        raise HTTPException(status_code=404, detail="Session not found.")

    deleted_filter = "" if include_deleted else "AND deleted_at IS NULL"
    rows = run_query(f"""
        SELECT id, sender, content, token_count, created_at, deleted_at
        FROM chat_messages
        WHERE session_id = %s {deleted_filter}
        ORDER BY created_at ASC
        LIMIT %s OFFSET %s
    """, (session_id, limit, offset))

    messages = []
    for r in rows:
        messages.append({
            "id": str(r["id"]),
            "sender": r["sender"],
            "content": r["content"],
            "token_count": r["token_count"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "is_deleted": r["deleted_at"] is not None,
        })

    return {"session_id": session_id, "messages": messages, "count": len(messages), "offset": offset}


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: SendMessageRequest, request: Request):
    user_id = _require_user(request)

    if body.sender not in ("user", "assistant"):
        raise HTTPException(status_code=400, detail="sender must be 'user' or 'assistant'.")

    session_check = run_query(
        "SELECT id FROM chat_sessions WHERE id = %s AND user_id = %s LIMIT 1",
        (session_id, user_id)
    )
    if not session_check:
        raise HTTPException(status_code=404, detail="Session not found.")

    import tiktoken
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        token_count = len(enc.encode(body.content))
    except Exception:
        token_count = 0

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO chat_messages (session_id, sender, content, token_count)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, sender, content, token_count, created_at
                """, (session_id, body.sender, body.content, token_count))
                row = cur.fetchone()
                conn.commit()

        return {
            "id": str(row[0]),
            "session_id": session_id,
            "sender": row[1],
            "content": row[2],
            "token_count": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
        }
    except Exception as e:
        logger.error(f"Send message error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}/messages/{message_id}")
async def soft_delete_message(session_id: str, message_id: str, request: Request):
    user_id = _require_user(request)

    session_check = run_query(
        "SELECT id FROM chat_sessions WHERE id = %s AND user_id = %s LIMIT 1",
        (session_id, user_id)
    )
    if not session_check:
        raise HTTPException(status_code=404, detail="Session not found.")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE chat_messages
                    SET deleted_at = NOW()
                    WHERE id = %s AND session_id = %s AND deleted_at IS NULL
                    RETURNING id
                """, (message_id, session_id))
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Message not found or already deleted.")
                conn.commit()
        return {"message": "Message deleted.", "message_id": message_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete message error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# STATS
# ============================================================================

@router.get("/stats")
async def chat_stats(request: Request):
    user_id = _require_user(request)
    rows = run_query("""
        SELECT
            COUNT(*) AS total_sessions,
            COALESCE(SUM(message_count), 0) AS total_messages,
            SUM(CASE WHEN is_archived = 1 THEN 1 ELSE 0 END) AS archived_sessions,
            MAX(last_activity) AS last_active
        FROM chat_sessions
        WHERE user_id = %s
    """, (user_id,))
    r = rows[0] if rows else {}
    return {
        "total_sessions": r.get("total_sessions", 0),
        "total_messages": r.get("total_messages", 0),
        "archived_sessions": r.get("archived_sessions", 0),
        "last_active": _iso_or_none(r.get("last_active")),
    }


def get_messages_func(session_id: str, limit: int = 20) -> list[dict]:
    rows = run_query(
        """
        SELECT sender, content, created_at
        FROM chat_messages
        WHERE session_id = %s AND deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (session_id, limit),
    )
    ordered = list(reversed(rows))
    return [{"role": row["sender"], "content": row["content"], "created_at": row.get("created_at")} for row in ordered]


def append_message_func(session_id: str, message: dict) -> Optional[str]:
    sender = message.get("role") or message.get("sender") or "user"
    content = message.get("content", "")

    import tiktoken

    try:
        enc = tiktoken.get_encoding("cl100k_base")
        token_count = len(enc.encode(content))
    except Exception:
        token_count = 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_messages (session_id, sender, content, token_count)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (session_id, sender, content, token_count),
            )
            row = cur.fetchone()
            conn.commit()
    return row[0] if row else None
