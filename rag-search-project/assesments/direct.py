# direct.py

import json
import logging
from uuid import UUID
from datetime import datetime
from typing import Any
from fastapi import APIRouter, HTTPException, Request, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from db.db import get_db_connection
from assesments.assesments_db import insert_assessment, delete_assessment

logger = logging.getLogger(__name__)
router = APIRouter(tags=["direct_routes"])


@router.get("/assessment/my-submissions")
async def get_my_submissions(request: Request):
    """Get all submissions made by the current user."""
    current_user_id = getattr(request.state, "user_id", None)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        s.id, s.assignment_id, s.assessment_id, s.submitted_at,
                        s.status, s.score, s.feedback,
                        a.title, a.subject
                    FROM assessment_submissions s
                    JOIN assessments a ON a.id = s.assessment_id
                    WHERE s.submitted_by = %s
                    ORDER BY s.submitted_at DESC;
                """, (str(current_user_id),))
                rows = cur.fetchall()

        return [
            {
                "submission_id": r[0],
                "assignment_id": r[1],
                "assessment_id": r[2],
                "submitted_at": r[3].isoformat() if hasattr(r[3], "isoformat") else r[3],
                "status": r[4],
                "score": r[5],
                "feedback": r[6],
                "title": r[7],
                "subject": r[8],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"get_my_submissions error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching submissions")


@router.get("/assessment/submissions/assigned-by-me")
async def get_submissions_assigned_by_me(request: Request):
    """Get all submissions for assessments assigned by the current user."""
    current_user_id = getattr(request.state, "user_id", None)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        s.id AS submission_id,
                        s.submitted_by,
                        u.display_name AS submitted_by_name,
                        s.submitted_at,
                        s.status,
                        s.score,
                        s.feedback,
                        a.title,
                        a.subject
                    FROM assessment_submissions s
                    JOIN assessment_assignments aa ON aa.id = s.assignment_id
                    JOIN assessments a ON a.id = s.assessment_id
                    JOIN users u ON u.id = s.submitted_by
                    WHERE aa.assigned_by = %s
                    ORDER BY s.submitted_at DESC;
                """, (str(current_user_id),))
                rows = cur.fetchall()

        return [
            {
                "submission_id": r[0],
                "submitted_by_id": r[1],
                "submitted_by": r[2],
                "submitted_at": r[3].isoformat() if hasattr(r[3], "isoformat") else r[3],
                "status": r[4],
                "score": r[5],
                "feedback": r[6],
                "title": r[7],
                "subject": r[8],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"get_submissions_assigned_by_me error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching submissions")


@router.get("/assessment/{assignment_id}")
async def get_assessment_by_assignment_id(assignment_id: str, request: Request):
    """Fetch the full assessment for a given assignment."""
    current_user_id = getattr(request.state, "user_id", None)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        a.id AS assignment_id,
                        asmt.id AS assessment_id,
                        asmt.title,
                        asmt.subject,
                        asmt.categories,
                        asmt.content,
                        u1.display_name AS assigned_by_name,
                        u2.display_name AS assigned_to_name,
                        a.priority,
                        a.status,
                        a.message,
                        a.created_at
                    FROM assessment_assignments a
                    JOIN assessments asmt ON asmt.id = a.assessment_id
                    JOIN users u1 ON u1.id = a.assigned_by
                    JOIN users u2 ON u2.id = a.assigned_to
                    WHERE a.id = %s;
                """, (assignment_id,))
                row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Assessment not found")

        return {
            "assignment_id": row[0],
            "assessment_id": row[1],
            "title": row[2],
            "subject": row[3],
            "categories": row[4] or [],
            "content": row[5],
            "assigned_by": row[6],
            "assigned_to": row[7],
            "priority": row[8],
            "status": row[9],
            "message": row[10],
            "created_at": row[11].isoformat() if hasattr(row[11], "isoformat") else row[11],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching assessment by assignment ID: {e}")
        raise HTTPException(status_code=500, detail="Error fetching assessment details")






class AssessmentSubmissionCreate(BaseModel):
    answers: dict[str, Any]


@router.post("/assessment/{assignment_id}/submit")
async def submit_assessment(assignment_id: str, payload: AssessmentSubmissionCreate, request: Request):
    """
    Save an assessment submission (answers) for a specific assignment.
    Marks the assignment as Completed.
    """
    current_user_id = getattr(request.state, "user_id", None)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:

                # Ensure assignment exists and belongs to the current user
                cur.execute("""
                    SELECT assessment_id, assigned_to, status 
                    FROM assessment_assignments 
                    WHERE id = %s;
                """, (assignment_id,))
                assignment = cur.fetchone()

                if not assignment:
                    raise HTTPException(status_code=404, detail="Assignment not found")

                assessment_id, assigned_to, status = assignment
                if str(assigned_to) != str(current_user_id):
                    raise HTTPException(status_code=403, detail="You are not allowed to submit this assessment")

                # Insert submission
                cur.execute("""
                    INSERT INTO assessment_submissions (
                        assignment_id, assessment_id, submitted_by, answers, status
                    )
                    VALUES (%s, %s, %s, %s, 'Submitted')
                    RETURNING id, submitted_at;
                """, (assignment_id, assessment_id, str(current_user_id), json.dumps(payload.answers)))
                submission = cur.fetchone()

                # Update assignment status
                cur.execute("""
                    UPDATE assessment_assignments
                    SET status = 'Completed', updated_at = now()
                    WHERE id = %s;
                """, (assignment_id,))

                conn.commit()

        return {
            "message": "Submission saved successfully",
            "submission_id": submission[0],
            "submitted_at": submission[1],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"submit_assessment error: {e}")
        raise HTTPException(status_code=500, detail="Error saving submission")
    


# ============================================================================
# Dynamic Link Routes
# ============================================================================

@router.post("/assessment/direct/verify-submission")
async def verify_direct_submission(request: Request):
    """
    Verify whether a specific user (assigned_to) has already submitted
    a dynamic assessment shared by another user (assigned_by).
    """
    try:
        body = await request.json()
        assessment_id = body.get("assessment_id")
        assigned_by_email = body.get("assigned_by")
        assigned_to_email = body.get("assigned_to")

        if not assessment_id or not assigned_by_email or not assigned_to_email:
            raise HTTPException(
                status_code=400,
                detail="Missing one or more required fields: assessment_id, assigned_by, assigned_to"
            )

        with get_db_connection() as conn, conn.cursor() as cur:
            # Get IDs for assigned_by and assigned_to
            cur.execute("SELECT id FROM users WHERE LOWER(email)=LOWER(%s);", (assigned_by_email,))
            assigned_by_row = cur.fetchone()
            if not assigned_by_row:
                raise HTTPException(status_code=404, detail=f"Assigned_by user not found: {assigned_by_email}")
            assigned_by_id = assigned_by_row[0]

            cur.execute("SELECT id FROM users WHERE LOWER(email)=LOWER(%s);", (assigned_to_email,))
            assigned_to_row = cur.fetchone()
            if not assigned_to_row:
                raise HTTPException(status_code=404, detail=f"Assigned_to user not found: {assigned_to_email}")
            assigned_to_id = assigned_to_row[0]

            # Check if assignment exists
            cur.execute("""
                SELECT id FROM assessment_assignments
                WHERE assessment_id = %s
                  AND assigned_by = %s
                  AND assigned_to = %s
                  AND message = 'Dynamic shared link submission';
            """, (assessment_id, assigned_by_id, assigned_to_id))
            assignment_row = cur.fetchone()

            if not assignment_row:
                # No assignment ever created → definitely not submitted
                return {"already_submitted": False}

            assignment_id = assignment_row[0]

            # Check if submission exists for this assignment
            cur.execute("""
                SELECT id, submitted_at, score
                FROM assessment_submissions
                WHERE assignment_id = %s
                ORDER BY submitted_at DESC
                LIMIT 1;
            """, (assignment_id,))
            submission = cur.fetchone()

            if submission:
                return {
                    "already_submitted": True,
                    "submission_id": submission[0],
                    "submitted_at": submission[1],
                    "score": submission[2]
                }
            else:
                return {"already_submitted": False}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[verify_direct_submission] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@router.get("/assessment/direct/{assessment_id}")
async def get_assessment_by_id(assessment_id: str, request: Request):
    """Fetch assessment details directly (for shareable links)"""
    current_user_id = getattr(request.state, "user_id", None)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, subject, categories, content, created_at
                FROM assessments
                WHERE id = %s;
            """, (assessment_id,))
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Assessment not found")

        return {
            "assessment_id": row[0],
            "title": row[1],
            "subject": row[2],
            "categories": row[3] or [],
            "content": row[4],
            "created_at": row[5],
        }

    except Exception as e:
        logger.exception(f"Error fetching assessment by ID: {e}")
        raise HTTPException(status_code=500, detail="Error fetching assessment details")


@router.post("/assessment/direct/{assessment_id}/submit")
async def submit_direct_assessment(
    assessment_id: str,
    request: Request,
    assigned_by: str | None = Query(None, description="Email of the user who shared the link"),
):
    """Submit a dynamic link assessment with percentage-based scoring."""
    current_user_id = getattr(request.state, "user_id", None)
    current_username = getattr(request.state, "username", None)
    if not current_user_id or not current_username:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        body = await request.json()
        answers = body.get("answers")

        # ✅ Validate payload
        if not isinstance(answers, list) or not answers:
            raise HTTPException(status_code=400, detail="Invalid 'answers' format. Expected a list.")

        if not assigned_by:
            raise HTTPException(status_code=400, detail="Missing 'assigned_by' query parameter")

        logger.info(
            f"[submit_direct_assessment] Start: assessment_id={assessment_id}, "
            f"assigned_by={assigned_by}, assigned_to={current_username}"
        )

        # ✅ Scoring system (percentage)
        total_marks = 0.0
        scored_marks = 0.0

        for item in answers:
            marks = float(item.get("marks", 0))
            user_answer = str(item.get("answer", "")).strip().lower()
            correct_answer = str(item.get("correct_answer", "")).strip().lower()

            total_marks += marks

            if correct_answer and user_answer == correct_answer:
                scored_marks += marks

        # ✅ Handle division by zero safely
        percentage_score = (scored_marks / total_marks * 100.0) if total_marks > 0 else 0.0
        percentage_score = round(percentage_score, 2)  # e.g., 83.33

        with get_db_connection() as conn, conn.cursor() as cur:
            # validate assessment
            cur.execute("SELECT id FROM assessments WHERE id = %s;", (assessment_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Assessment not found")

            # lookup assigned_by user id
            cur.execute("SELECT id FROM users WHERE LOWER(email)=LOWER(%s);", (assigned_by,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Assigned_by user not found: {assigned_by}"
                )
            assigned_by_id = row[0]

            # prevent self-submission
            if str(assigned_by_id) == str(current_user_id):
                raise HTTPException(
                    status_code=400,
                    detail="You cannot submit your own shared assessment"
                )

            # create assignment
            cur.execute("""
                INSERT INTO assessment_assignments (
                    assessment_id, assigned_by, assigned_to, priority, status, message
                )
                VALUES (%s, %s, %s, 'Medium', 'Completed', 'Dynamic shared link submission')
                RETURNING id;
            """, (assessment_id, assigned_by_id, current_user_id))
            assignment_id = cur.fetchone()[0]

            # ✅ Save percentage score (float)
            cur.execute("""
                INSERT INTO assessment_submissions (
                    assignment_id, assessment_id, submitted_by, answers, score, status
                )
                VALUES (%s, %s, %s, %s, %s, 'Submitted')
                RETURNING id, submitted_at;
            """, (
                assignment_id,
                assessment_id,
                current_user_id,
                json.dumps(answers),
                percentage_score
            ))

            submission_id, submitted_at = cur.fetchone()
            conn.commit()

        logger.info(
            f"[submit_direct_assessment] Success: submission_id={submission_id}, "
            f"by={current_username}, assigned_by={assigned_by}, score={percentage_score}%"
        )

        return {
            "message": "Submission saved successfully (dynamic link)",
            "submission_id": submission_id,
            "submitted_at": submitted_at,
            "score_percentage": percentage_score,   # ✅ e.g. 83.33
            "scored_marks": scored_marks,
            "total_marks": total_marks
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[submit_direct_assessment] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")



@router.get("/assessment/direct/submissions/assigned-by-me")
async def get_direct_submissions_assigned_by_me(request: Request):
    """Return dynamic link submissions shared by current user, grouped by assessment."""
    current_user_id = getattr(request.state, "user_id", None)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    s.id AS submission_id,
                    s.assessment_id,
                    s.submitted_by,
                    u2.display_name AS submitted_by_name,
                    s.submitted_at,
                    s.status,
                    s.score,
                    a.title AS assessment_title,
                    a.subject,
                    asmt_assign.message
                FROM assessment_submissions s
                JOIN assessment_assignments asmt_assign 
                    ON asmt_assign.id = s.assignment_id
                JOIN assessments a 
                    ON a.id = s.assessment_id
                JOIN users u2 
                    ON u2.id = s.submitted_by
                WHERE asmt_assign.assigned_by = %s
                  AND asmt_assign.message = 'Dynamic shared link submission'
                ORDER BY s.submitted_at DESC;
            """, (str(current_user_id),))

            rows = cur.fetchall()

        # Grouping by assessment
        assessment_map = {}

        for r in rows:
            assessment_id = r[1]

            if assessment_id not in assessment_map:
                assessment_map[assessment_id] = {
                    "assessment_id": assessment_id,
                    "assessment_name": r[7],
                    "subject": r[8],
                    "people": []
                }

            assessment_map[assessment_id]["people"].append({
                "submission_id": r[0],
                "submitted_by_id": r[2],
                "submitted_by_name": r[3],
                "submitted_at": r[4],
                "status": r[5],
                "score": r[6],
                "message": r[9]
            })

        return list(assessment_map.values())

    except Exception as e:
        logger.exception(f"get_direct_submissions_assigned_by_me error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching dynamic submissions")



@router.delete("/assessment/direct/{assessment_id}/delete-submissions")
async def delete_direct_submissions(
    assessment_id: str,
    request: Request,
    assigned_by: str = Query(..., description="Email of the user who shared the link (teacher)")
):
    """
    Delete all submissions for a specific assessment that were shared
    by a particular teacher (assigned_by). Only the teacher can perform this action.
    """
    current_user_id = getattr(request.state, "user_id", None)
    current_username = getattr(request.state, "username", None)

    if not current_user_id or not current_username:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        if current_username.lower() != assigned_by.lower():
            raise HTTPException(
                status_code=403,
                detail="You are not authorized to delete submissions for this assessment"
            )

        with get_db_connection() as conn, conn.cursor() as cur:
            # ✅ Lookup the teacher’s ID
            cur.execute("SELECT id FROM users WHERE LOWER(email)=LOWER(%s);", (assigned_by,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Assigned_by user not found: {assigned_by}")
            assigned_by_id = row[0]

            # ✅ Verify the teacher actually assigned this assessment
            cur.execute("""
                SELECT id FROM assessment_assignments
                WHERE assessment_id = %s
                  AND assigned_by = %s
                  AND message = 'Dynamic shared link submission';
            """, (assessment_id, assigned_by_id))
            assignment_rows = cur.fetchall()

            if not assignment_rows:
                raise HTTPException(
                    status_code=404,
                    detail="No matching dynamic link assignments found for this teacher and assessment"
                )

            assignment_ids = [r[0] for r in assignment_rows]

            # ✅ Delete submissions tied to those assignments
            placeholders = ", ".join(["%s"] * len(assignment_ids))
            cur.execute(
                f"DELETE FROM assessment_submissions WHERE assignment_id IN ({placeholders}) RETURNING id;",
                tuple(assignment_ids),
            )
            deleted_rows = cur.fetchall()

            # ✅ Optionally delete assignment records too
            cur.execute(
                f"DELETE FROM assessment_assignments WHERE id IN ({placeholders});",
                tuple(assignment_ids),
            )

            conn.commit()

        deleted_count = len(deleted_rows)
        logger.info(
            f"[delete_direct_submissions] Deleted {deleted_count} submission(s) for assessment_id={assessment_id}, assigned_by={assigned_by}"
        )

        return {
            "message": f"Successfully deleted {deleted_count} submission(s)",
            "deleted_count": deleted_count,
            "assessment_id": assessment_id,
            "assigned_by": assigned_by
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[delete_direct_submissions] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    


# ============================================================================
# ASSESSMENT 
# ============================================================================


# Define the Pydantic model for the incoming request body
class AssessmentRequest(BaseModel):
    assessment: dict
    title: str
    subject: str
    categories: list
    username: str


@router.post("/ingest_assessment")
async def ingest_assessment(
    request: Request,
    assessment_data: AssessmentRequest,
    background_tasks: BackgroundTasks
):
    """Ingest an assessment into the system."""
    current_username = getattr(request.state, "username", None)
    if not current_username:
        raise HTTPException(status_code=401, detail="Unauthorized")

    assessment = assessment_data.assessment
    title = assessment_data.title
    subject = assessment_data.subject
    categories = assessment_data.categories
    username = current_username

    if not assessment or not title or not subject or not username:
        raise HTTPException(status_code=400, detail="Missing required fields: 'assessment', 'title', 'subject', or 'username'")

    # Skip database query for now (you can add this back later)
    # Just send a simplified response and add a background task for processing
    background_tasks.add_task(insert_assessment, {
        "assessment": assessment,
        "title": title,
        "subject": subject,
        "categories": categories,
        "username": username
    })

    return {"message": f"Ingestion started for assessment titled '{title}' by '{username}'."}


@router.get("/explore_assessments/filters")
async def get_filters():
    """
    Fetch all unique subjects and categories for filtering.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT subject FROM assessments")
            subjects = [r[0] for r in cur.fetchall()]

            cur.execute("SELECT categories FROM assessments")
            categories = sorted(
                {
                    category
                    for row in cur.fetchall()
                    for category in (row.get("categories") or [])
                    if str(category).strip()
                }
            )

    return {"subjects": subjects, "categories": categories}

@router.get("/explore_assessments")
async def explore_assessments(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    subject: str | None = None,
    category: str | None = None,
    search: str | None = None
):
    """
    Paginated & filtered assessment exploration.
    """
    offset = (page - 1) * limit
    filters = []
    params = []

    if subject:
        filters.append("LOWER(subject) LIKE %s")
        params.append(f"%{subject.lower()}%")

    if category:
        filters.append("LOWER(categories) LIKE %s")
        params.append(f'%"{category.lower()}"%')

    if search:
        filters.append("(LOWER(title) LIKE %s OR LOWER(content) LIKE %s)")
        params += [f"%{search.lower()}%", f"%{search.lower()}%"]

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            query = f"""
                SELECT id, username, title, subject, categories, content, created_at
                FROM assessments
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """
            cur.execute(query, (*params, limit, offset))
            rows = cur.fetchall()

            cur.execute(f"SELECT COUNT(*) FROM assessments {where_clause}", params)
            total = cur.fetchone()[0]

    assessments = [
        {
            "id": r[0],
            "username": r[1],
            "title": r[2],
            "subject": r[3],
            "categories": r[4],
            "content": r[5],  # ✅ Added content here
            "created_at": r[6].isoformat() if isinstance(r[6], datetime) else r[6],
        }
        for r in rows
    ]

    return {"assessments": assessments, "total": total, "page": page, "limit": limit}


    
@router.delete("/delete_assessment/{assessment_id}")
async def delete_assessment_endpoint(assessment_id: UUID, request: Request):
    """
    Delete an assessment by ID — only if it belongs to the requesting user.
    Expects JSON body: {"username": "<user_email>"}
    """
    try:
        data = await request.json()
        username = data.get("username")

        if not username:
            raise HTTPException(status_code=400, detail="Username is required.")

        # Convert UUID to string before passing to DB
        success = delete_assessment(str(assessment_id), username)

        if not success:
            raise HTTPException(status_code=403, detail="Not authorized or assessment not found.")

        return JSONResponse(content={"message": f"Assessment {assessment_id} deleted successfully."}, status_code=200)

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting assessment: {str(e)}")
