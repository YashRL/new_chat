import time

from db.db import get_db_connection
from ingest.ingest import insert_document


def test_insert_document_preserves_valid_audit_user():
    file_hash = f"test-valid-audit-{time.time_ns()}"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            doc_id = insert_document(
                cur,
                {"title": "Valid Audit User"},
                "test.pdf",
                "pdf",
                [],
                file_hash,
                created_by="user-guest",
                updated_by="user-guest",
                visibility={"everyone": True},
            )

            cur.execute("SELECT created_by, updated_by FROM documents WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            assert row["created_by"] == "user-guest"
            assert row["updated_by"] == "user-guest"
            conn.rollback()


def test_insert_document_ignores_unknown_audit_user():
    file_hash = f"test-invalid-audit-{time.time_ns()}"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            doc_id = insert_document(
                cur,
                {"title": "Invalid Audit User"},
                "test.pdf",
                "pdf",
                [],
                file_hash,
                created_by="missing-user-id",
                updated_by="missing-user-id",
                visibility={"everyone": True},
            )

            cur.execute("SELECT created_by, updated_by FROM documents WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            assert row["created_by"] is None
            assert row["updated_by"] is None
            conn.rollback()
