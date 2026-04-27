import json
from datetime import datetime
from db.db import get_db_connection


def insert_assessment(data: dict):
    """
    Insert a new assessment into the database.
    
    data: The input JSON object containing the assessment details.
    """
    # Extract fields from input data
    assessment = data.get("assessment")
    title = data.get("title")
    subject = data.get("subject")
    categories = data.get("categories", [])
    username = data.get("username")

    if not assessment or not title or not subject or not username:
        raise ValueError("Missing required fields: 'assessment', 'title', 'subject', or 'username'")

    # Fetch user_id based on username (email ID)
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s LIMIT 1", (username,))
            user = cur.fetchone()
            
            if not user:
                raise ValueError(f"User with username '{username}' not found.")
            
            user_id = user[0]

            # Get current timestamp for created_at and updated_at
            current_timestamp = datetime.utcnow()

            # Insert into the assessments table
            cur.execute("""
                INSERT INTO assessments (user_id, username, title, subject, categories, content, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                user_id,
                username,
                title,
                subject,
                categories,
                json.dumps(assessment),  # Store the assessment as JSONB
                current_timestamp,
                current_timestamp
            ))

            # Commit and return the new assessment ID
            assessment_id = cur.fetchone()[0]
            conn.commit()
            return assessment_id


def delete_assessment(assessment_id: str, username: str):
    """
    Delete an assessment by ID (UUID string), only if it belongs to the given username.
    """
    if not assessment_id or not username:
        raise ValueError("Missing required fields: 'assessment_id' and 'username'")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM assessments
                WHERE id = %s AND username = %s
                LIMIT 1
            """, (assessment_id, username))
            
            record = cur.fetchone()
            if not record:
                return False

            cur.execute("DELETE FROM assessments WHERE id = %s", (assessment_id,))
            conn.commit()
            return True
