import logging
import bcrypt
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import JSONResponse
from db.db import get_db_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("/data")
async def get_profile_data(request: Request):
    """Fetch current user data"""
    username = request.state.username

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email, display_name FROM users WHERE email = %s;",
                    (username,)
                )
                row = cur.fetchone()

                if not row:
                    raise HTTPException(status_code=404, detail="User not found")

                email, display_name = row
                return {"email": email, "display_name": display_name}

    except Exception as e:
        logger.error(f"Error fetching profile: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch profile")


@router.post("/update")
async def update_profile(
    request: Request,
    display_name: str = Form(None),   # <<< FIXED (optional)
    password: str = Form(None)        # already optional
):
    username = request.state.username

    if not display_name and not password:
        raise HTTPException(
            status_code=400,
            detail="No fields provided to update"
        )

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:

                # Build dynamic update query
                updates = []
                values = []

                if display_name:
                    updates.append("display_name = %s")
                    values.append(display_name)

                if password:
                    hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                    updates.append("password = %s")
                    values.append(hashed_pw)

                # append username at end for WHERE
                values.append(username)

                query = f"UPDATE users SET {', '.join(updates)} WHERE email = %s;"
                cur.execute(query, tuple(values))

                conn.commit()

        return JSONResponse({"message": "Profile updated successfully"})

    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        raise HTTPException(status_code=500, detail="Failed to update profile")
