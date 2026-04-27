# assessments.py

import logging
from fastapi import APIRouter, HTTPException, Request
from db.db import get_db_connection
from pydantic import BaseModel
from uuid import UUID as UUIDType

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Assessments"])

class AssignmentCreate(BaseModel):
    assessment_id: UUIDType
    assigned_to: UUIDType
    priority: str | None = "Medium"
    message: str | None = None

# Assign assessment to a user
@router.post("/assessments/assign")
async def assign_assessment(request: Request, payload: AssignmentCreate):
    current_user_id = getattr(request.state, "user_id", None)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO assessment_assignments (
                        assessment_id, assigned_by, assigned_to, priority, message
                    ) VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, created_at;
                """, (
                    str(payload.assessment_id),
                    str(current_user_id),
                    str(payload.assigned_to),
                    payload.priority or "Medium",
                    payload.message
                ))
                row = cur.fetchone()
                conn.commit()

        return {"message": "Assessment assigned successfully", "id": row[0], "created_at": row[1]}
    except Exception as e:
        logger.error(f"assign_assessment error: {e}")
        raise HTTPException(status_code=500, detail="Error assigning assessment")

# Get all assignments for current user
@router.get("/assessments/assigned-to-me")
async def get_my_assignments(request: Request):
    current_user_id = getattr(request.state, "user_id", None)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        aa.id,
                        ass.title,
                        ass.subject,
                        aa.priority,
                        aa.status,
                        aa.message,
                        aa.created_at,
                        u.display_name AS assigned_by
                    FROM assessment_assignments aa
                    JOIN assessments ass ON ass.id = aa.assessment_id
                    JOIN users u ON u.id = aa.assigned_by
                    WHERE aa.assigned_to = %s
                    ORDER BY aa.created_at DESC;
                """, (str(current_user_id),))
                rows = cur.fetchall()

        return [
            {
                "assignment_id": r[0],
                "title": r[1],
                "subject": r[2],
                "priority": r[3],
                "status": r[4],
                "message": r[5],
                "created_at": r[6].isoformat() if hasattr(r[6], "isoformat") else r[6],
                "assigned_by": r[7],
            }
            for r in rows
        ]

    except Exception as e:
        logger.error(f"get_my_assignments error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching assignments")



@router.get("/assessments/assessments")
async def get_all_assessments(request: Request):
    """
    Fetch all assessments (for Admins only).
    """
    role = getattr(request.state, "role", None)
    if not role or role.lower() != "admin":
        raise HTTPException(status_code=403, detail="Access denied. Admins only.")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, title, subject, categories, created_at
                    FROM assessments
                    ORDER BY created_at DESC;
                """)
                rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "title": r[1],
                "subject": r[2],
                "categories": r[3],
                "created_at": r[4]
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"get_all_assessments error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching assessments")


@router.get("/assessments/assigned-by-me")
async def get_assessments_assigned_by_me(request: Request):
    """
    Get all assessments assigned by the current (admin) user.
    """
    current_user_id = getattr(request.state, "user_id", None)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        aa.id,
                        ass.title,
                        ass.subject,
                        u.display_name AS assigned_to,
                        aa.priority,
                        aa.status,
                        aa.message,
                        aa.created_at
                    FROM assessment_assignments aa
                    JOIN assessments ass ON ass.id = aa.assessment_id
                    JOIN users u ON u.id = aa.assigned_to
                    WHERE aa.assigned_by = %s
                    ORDER BY aa.created_at DESC;
                """, (str(current_user_id),))
                rows = cur.fetchall()

        return [
            {
                "assignment_id": r[0],
                "title": r[1],
                "subject": r[2],
                "assigned_to": r[3],
                "priority": r[4],
                "status": r[5],
                "message": r[6],
                "created_at": r[7].isoformat() if hasattr(r[7], "isoformat") else r[7],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"get_assessments_assigned_by_me error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching assigned assessments")

