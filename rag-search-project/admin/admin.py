# admin.py

import logging
from fastapi import APIRouter, HTTPException, Request
from db.db import get_db_connection
from fastapi import Path
from pydantic import BaseModel
from uuid import uuid4, UUID as UUIDType

# -------------------------
# Models
# -------------------------
class RoleCreate(BaseModel):
    name: str
    description: str | None = None

class RoleAssign(BaseModel):
    role_name: str


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


# ============================================================================
# ADMIN ROUTES
# ============================================================================
def require_admin(request: Request):
    current_user_email = getattr(request.state, "username", None)
    current_user_role = getattr(request.state, "role", None)

    if not (current_user_role and current_user_role.lower() == "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/users")
async def get_all_users(request: Request):
    """
    Admin-only route to list all registered users and their roles.
    """
    require_admin(request)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # ✅ Join roles via folder_acl to include user roles
                cur.execute("""
                    SELECT
                        u.id,
                        u.email,
                        u.display_name,
                        u.created_at,
                        COALESCE(r.name, 'User') AS role
                    FROM users u
                    LEFT JOIN folder_acl fa ON fa.user_id = u.id
                    LEFT JOIN roles r ON r.id = fa.role_id
                    ORDER BY u.created_at DESC;
                """)
                rows = cur.fetchall()

        users = [
            {
                "id": r[0],
                "email": r[1],
                "display_name": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
                "role": r[4]
            }
            for r in rows
        ]

        return {"total": len(users), "users": users}

    except Exception as e:
        logger.error(f"get_all_users error: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching users: {str(e)}")


# -------------------------
# Get single user by id
# -------------------------
@router.get("/users/{user_id}")
async def get_user(request: Request, user_id: UUIDType = Path(...)):
    require_admin(request)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, display_name, created_at FROM users WHERE id = %s;",
                    (str(user_id),)
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="User not found")

                # try to find a role for the user (if permission/role assignments exist)
                cur.execute(
                    """
                    SELECT r.name
                    FROM roles r
                    JOIN folder_acl fa ON fa.role_id = r.id
                    WHERE fa.user_id = %s
                    LIMIT 1;
                    """,
                    (str(user_id),)
                )
                r = cur.fetchone()
                role_name = r[0] if r else "User"

        user = {
            "id": str(row[0]),
            "email": row[1],
            "display_name": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
            "role": role_name
        }
        return user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"admin get_user error: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching user: {str(e)}")


# -------------------------
# Get documents for user
# -------------------------
@router.get("/users/{user_id}/documents")
async def get_user_documents(request: Request, user_id: UUIDType = Path(...)):
    require_admin(request)


    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, document_name, document_type, created_at, visibility, total_tokens
                    FROM documents
                    WHERE created_by = (SELECT id FROM users WHERE id = %s)
                    ORDER BY created_at DESC;
                    """,
                    (str(user_id),)
                )
                rows = cur.fetchall()

        docs = [
            {
                "id": str(r[0]),
                "document_name": r[1],
                "document_type": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
                "visibility": r[4],
                "total_tokens": r[5],
            }
            for r in rows
        ]
        return docs

    except Exception as e:
        logger.error(f"admin get_user_documents error: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching documents: {str(e)}")


# -------------------------
# Get assessments for user
# -------------------------
@router.get("/users/{user_id}/assessments")
async def get_user_assessments(request: Request, user_id: UUIDType = Path(...)):
    require_admin(request)


    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # assessments table stores user_id (UUID) and username; we query by user_id
                cur.execute(
                    """
                    SELECT id, username, title, subject, categories, content, created_at
                    FROM assessments
                    WHERE user_id = %s
                    ORDER BY created_at DESC;
                    """,
                    (str(user_id),)
                )
                rows = cur.fetchall()

        assessments = [
            {
                "id": str(r[0]),
                "username": r[1],
                "title": r[2],
                "subject": r[3],
                "categories": r[4],
                "content": r[5],
                "created_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ]
        return assessments

    except Exception as e:
        logger.error(f"admin get_user_assessments error: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching assessments: {str(e)}")


# -------------------------
# Create a new role
# -------------------------
@router.post("/roles")
async def create_role(request: Request, payload: RoleCreate):
    require_admin(request)


    name = payload.name.strip().capitalize()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Check if role already exists
                cur.execute("SELECT id FROM roles WHERE LOWER(name) = LOWER(%s);", (name,))
                if cur.fetchone():
                    raise HTTPException(status_code=400, detail="Role already exists")

                new_id = str(uuid4())
                cur.execute(
                    "INSERT INTO roles (id, name, description, created_at) VALUES (%s, %s, %s, NOW());",
                    (new_id, name, payload.description),
                )
                conn.commit()

        return {"id": new_id, "name": name, "description": payload.description}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_role error: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating role: {str(e)}")


# -------------------------
# Assign a role to a user
# -------------------------
@router.put("/users/{user_id}/role")
async def assign_user_role(request: Request, user_id: UUIDType, payload: RoleAssign):
    require_admin(request)

    role_name = payload.role_name.strip().capitalize()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # ✅ Validate role
                cur.execute("SELECT id FROM roles WHERE LOWER(name) = LOWER(%s);", (role_name,))
                role_row = cur.fetchone()
                if not role_row:
                    raise HTTPException(status_code=404, detail="Role not found")
                role_id = role_row[0]

                # ✅ Validate user
                cur.execute("SELECT id FROM users WHERE id = %s;", (str(user_id),))
                user_row = cur.fetchone()
                if not user_row:
                    raise HTTPException(status_code=404, detail="User not found")

                # ✅ Clear any old role mappings
                cur.execute("DELETE FROM folder_acl WHERE user_id = %s;", (str(user_id),))

                # ✅ Assign new role with unique global resource_id
                global_resource_id = str(uuid4())
                default_permission = "full_access"

                cur.execute(
                    """
                    INSERT INTO folder_acl (
                        id, resource_type, resource_id, user_id, permission, role_id, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW());
                    """,
                    (
                        str(uuid4()),
                        "global",
                        global_resource_id,
                        str(user_id),
                        default_permission,
                        role_id,
                    ),
                )

                conn.commit()

        return {
            "user_id": str(user_id),
            "new_role": role_name,
            "permission": default_permission,
            "resource_id": global_resource_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"assign_user_role error: {e}")
        raise HTTPException(status_code=500, detail=f"Error assigning role: {str(e)}")


# -------------------------
# Get all available roles
# -------------------------
@router.get("/roles")
async def list_roles(request: Request):
    """
    Admin-only route to list all roles in the system.
    """
    require_admin(request)


    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, description, created_at
                    FROM roles
                    ORDER BY created_at ASC;
                """)
                rows = cur.fetchall()

        roles = [
            {
                "id": str(r[0]),
                "name": r[1],
                "description": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
            }
            for r in rows
        ]

        return {"total": len(roles), "roles": roles}

    except Exception as e:
        logger.error(f"list_roles error: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching roles: {str(e)}")