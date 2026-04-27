import sys
import os
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from db.db import get_db_connection


def make_admin(email: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("SELECT id, email, display_name FROM users WHERE LOWER(email) = LOWER(%s);", (email,))
            user = cur.fetchone()
            if not user:
                print(f"[ERROR] No user found with email: {email}")
                sys.exit(1)

            user_id, user_email, display_name = user
            print(f"[INFO] Found user: {user_email} (id={user_id}, display_name={display_name})")

            cur.execute("SELECT id FROM roles WHERE LOWER(name) = 'admin' LIMIT 1;")
            role = cur.fetchone()
            if not role:
                print("[ERROR] 'Admin' role not found in roles table. Run migrations first.")
                sys.exit(1)

            role_id = role[0]

            cur.execute("SELECT role_id FROM folder_acl WHERE user_id = %s LIMIT 1;", (str(user_id),))
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    "SELECT name FROM roles WHERE id = %s;",
                    (existing[0],)
                )
                existing_role = cur.fetchone()
                existing_role_name = existing_role[0] if existing_role else "Unknown"
                print(f"[INFO] User currently has role: {existing_role_name} — replacing with Admin")
                cur.execute("DELETE FROM folder_acl WHERE user_id = %s;", (str(user_id),))
            else:
                print("[INFO] User has no role assigned — assigning Admin")

            cur.execute(
                """
                INSERT INTO folder_acl (id, resource_type, resource_id, user_id, permission, role_id, created_at)
                VALUES (%s, 'global', %s, %s, 'full_access', %s, CURRENT_TIMESTAMP);
                """,
                (str(uuid.uuid4()), str(uuid.uuid4()), str(user_id), str(role_id))
            )

            conn.commit()
            print(f"[SUCCESS] {user_email} is now an Admin. They must log in again to get an updated token.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/make_admin.py <email>")
        print("Example: python scripts/make_admin.py john@example.com")
        sys.exit(1)

    make_admin(sys.argv[1])
