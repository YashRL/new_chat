# auth.py

import os
import logging
import datetime
import bcrypt
import jwt
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from db.db import get_db_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])

SECRET_KEY = os.getenv("SECRET_KEY", "your_super_secret_key")


@router.post("/signup")
async def signup(username: str = Form(...), password: str = Form(...)):
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE email = %s;", (username,))
                if cur.fetchone():
                    raise HTTPException(status_code=400, detail="Username already exists")
                cur.execute(
                    "INSERT INTO users (email, password, display_name) VALUES (%s, %s, %s);",
                    (username, hashed, username)
                )
                conn.commit()
        return JSONResponse({"message": "Signup successful"})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signup error: {e}")
        raise HTTPException(status_code=500, detail="Signup failed")


@router.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, password, display_name FROM users WHERE email = %s;",
                    (username,)
                )
                row = cur.fetchone()

                if not row or not bcrypt.checkpw(password.encode(), row[1].encode()):
                    raise HTTPException(status_code=401, detail="Invalid credentials")

                user_id, _, display_name = row

                cur.execute("""
                    SELECT r.name
                    FROM roles r
                    JOIN folder_acl fa ON fa.role_id = r.id
                    WHERE fa.user_id = %s
                    LIMIT 1;
                """, (str(user_id),))
                r = cur.fetchone()
                role_name = r[0] if r else "User"

        payload = {
            "username": username,
            "user_id": str(user_id),
            "display_name": display_name,
            "role": role_name,
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=6)
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")

        return JSONResponse({
            "message": "Login successful",
            "username": username,
            "display_name": display_name,
            "role": role_name,
            "user_id": str(user_id),
            "token": token
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail="Login failed")


@router.post("/logout")
async def logout(request: Request):
    return JSONResponse({"message": "Logged out"})


@router.get("/check")
async def check_session(request: Request):
    username = getattr(request.state, "username", None)
    if username:
        return {"logged_in": True, "username": username}
    return {"logged_in": False}


class UpdateProfileRequest(BaseModel):
    display_name: str | None = None
    current_password: str | None = None
    new_password: str | None = None


@router.post("/update-profile")
async def update_profile(request: Request, request_data: UpdateProfileRequest):
    username = getattr(request.state, "username", None)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                updates = []
                params = []

                if request_data.display_name:
                    updates.append("display_name = %s")
                    params.append(request_data.display_name)

                if request_data.new_password:
                    if not request_data.current_password:
                        raise HTTPException(status_code=400, detail="Current password required to set a new password")
                    cur.execute("SELECT password FROM users WHERE email = %s;", (username,))
                    row = cur.fetchone()
                    if not row or not bcrypt.checkpw(request_data.current_password.encode(), row[0].encode()):
                        raise HTTPException(status_code=401, detail="Current password is incorrect")
                    hashed = bcrypt.hashpw(request_data.new_password.encode(), bcrypt.gensalt()).decode()
                    updates.append("password = %s")
                    params.append(hashed)

                if not updates:
                    raise HTTPException(status_code=400, detail="No changes provided")

                params.append(username)
                query = f"UPDATE users SET {', '.join(updates)} WHERE email = %s;"
                cur.execute(query, tuple(params))
                conn.commit()

        return JSONResponse({"message": "Profile updated successfully"})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update profile error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update profile")
