import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
DEFAULT_DB_PATH = os.path.join(PROJECT_DIR, "knowledge.db")
DB_PATH = os.path.abspath(os.getenv("SQLITE_PATH", DEFAULT_DB_PATH))
MIGRATIONS_DIR = os.getenv("MIGRATIONS_DIR", os.path.join(BASE_DIR, "migrations"))

JSON_COLUMNS = {
    "answers",
    "categories",
    "content",
    "document_keywords",
    "embedding",
    "keywords",
    "members",
    "meta",
    "metadata",
    "visibility",
}
TIMESTAMP_COLUMNS = {
    "applied_at",
    "created_at",
    "deleted_at",
    "document_created_at",
    "expiry_date",
    "last_activity",
    "submitted_at",
    "updated_at",
    "uploaded_at",
}

_init_lock = threading.Lock()
_initialized = False


def _adapt_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _convert_timestamp(value: bytes) -> datetime:
    text = value.decode("utf-8")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_adapter(date, lambda v: v.isoformat())
sqlite3.register_converter("TIMESTAMP", _convert_timestamp)


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    email TEXT UNIQUE,
    password TEXT,
    display_name TEXT,
    blendID INTEGER,
    successfactorID INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_groups (
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    group_id TEXT REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);

CREATE TABLE IF NOT EXISTS folder_acl (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    group_id TEXT REFERENCES groups(id) ON DELETE CASCADE,
    role_id TEXT REFERENCES roles(id) ON DELETE CASCADE,
    permission TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    document_name TEXT NOT NULL,
    document_type TEXT NOT NULL,
    keywords TEXT DEFAULT '[]',
    meta TEXT DEFAULT '{}',
    total_tokens INTEGER DEFAULT 0,
    file_hash TEXT UNIQUE,
    text_hash TEXT UNIQUE,
    minhash_sig BLOB,
    simhash INTEGER,
    semantic_hash TEXT,
    canonical_doc_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
    version_of TEXT REFERENCES documents(id) ON DELETE SET NULL,
    version_label TEXT,
    dedup_status TEXT DEFAULT 'new',
    source_path TEXT,
    mime_type TEXT,
    language TEXT,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    visibility TEXT DEFAULT '{"everyone": true}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sections (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES sections(id) ON DELETE CASCADE,
    title TEXT,
    order_index INTEGER,
    level INTEGER,
    meta TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS headings (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    section_id TEXT REFERENCES sections(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title TEXT,
    order_index INTEGER,
    meta TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paragraphs (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_id TEXT REFERENCES sections(id) ON DELETE SET NULL,
    heading_id TEXT REFERENCES headings(id) ON DELETE SET NULL,
    text TEXT NOT NULL,
    norm_text TEXT,
    token_count INTEGER DEFAULT 0,
    char_start INTEGER,
    char_end INTEGER,
    page_number INTEGER,
    paragraph_index INTEGER,
    chunk_hash TEXT,
    embedding_id TEXT,
    dup_cluster_id TEXT,
    dedup_score REAL DEFAULT 0.0,
    meta TEXT DEFAULT '{}',
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    parent_chunk_id TEXT REFERENCES paragraphs(id) ON DELETE SET NULL,
    chunk_type TEXT DEFAULT 'standalone',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_id, chunk_hash)
);

CREATE TABLE IF NOT EXISTS embeddings (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    paragraph_id TEXT NOT NULL UNIQUE REFERENCES paragraphs(id) ON DELETE CASCADE,
    embedding TEXT,
    model_name TEXT,
    model_version TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dedup_clusters (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    canonical_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    members TEXT DEFAULT '[]',
    dedup_score REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assessments (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    title TEXT NOT NULL,
    subject TEXT NOT NULL,
    categories TEXT DEFAULT '[]',
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assessment_assignments (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    assigned_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_to TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    priority TEXT DEFAULT 'Medium',
    message TEXT,
    status TEXT DEFAULT 'Pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assessment_submissions (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    assignment_id TEXT NOT NULL REFERENCES assessment_assignments(id) ON DELETE CASCADE,
    assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    submitted_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    answers TEXT NOT NULL,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'Submitted',
    feedback TEXT,
    score REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    title TEXT DEFAULT 'New Chat',
    model TEXT DEFAULT 'gpt-4.1',
    metadata TEXT DEFAULT '{}',
    is_archived INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    session_id TEXT REFERENCES chat_sessions(id) ON DELETE CASCADE,
    sender TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT,
    token_count INTEGER DEFAULT 0,
    deleted_at TIMESTAMP DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_session_shares (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    session_id TEXT REFERENCES chat_sessions(id) ON DELETE CASCADE,
    shared_with_user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    permission TEXT DEFAULT 'view',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, shared_with_user_id)
);

CREATE TABLE IF NOT EXISTS document_files (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    document_id TEXT NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
    file_data BLOB NOT NULL,
    filename TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expiry_date TIMESTAMP,
    mime_type TEXT DEFAULT 'application/pdf'
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(document_type);
CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_documents_text_hash ON documents(text_hash);
CREATE INDEX IF NOT EXISTS idx_documents_semantic_hash ON documents(semantic_hash);
CREATE INDEX IF NOT EXISTS idx_paragraphs_document_id ON paragraphs(document_id);
CREATE INDEX IF NOT EXISTS idx_paragraphs_page_number ON paragraphs(page_number);
CREATE INDEX IF NOT EXISTS idx_paragraphs_document_chunk ON paragraphs(document_id, chunk_hash);
CREATE INDEX IF NOT EXISTS idx_paragraphs_chunk_hash_lookup ON paragraphs(chunk_hash);
CREATE INDEX IF NOT EXISTS idx_paragraphs_parent_chunk_id ON paragraphs(parent_chunk_id);
CREATE INDEX IF NOT EXISTS idx_paragraphs_chunk_type ON paragraphs(chunk_type);
CREATE INDEX IF NOT EXISTS idx_document_files_doc_id ON document_files(document_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated ON chat_sessions(user_id, updated_at DESC, is_archived);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created ON chat_messages(session_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_assessments_user_id ON assessments(user_id);
CREATE INDEX IF NOT EXISTS idx_assessment_assignments_assessment_id ON assessment_assignments(assessment_id);
CREATE INDEX IF NOT EXISTS idx_assessment_assignments_assigned_to ON assessment_assignments(assigned_to);
CREATE INDEX IF NOT EXISTS idx_assessment_assignments_assigned_by ON assessment_assignments(assigned_by);
CREATE INDEX IF NOT EXISTS idx_assessment_submissions_assignment_id ON assessment_submissions(assignment_id);
CREATE INDEX IF NOT EXISTS idx_assessment_submissions_assessment_id ON assessment_submissions(assessment_id);
CREATE INDEX IF NOT EXISTS idx_assessment_submissions_submitted_by ON assessment_submissions(submitted_by);

CREATE TRIGGER IF NOT EXISTS trg_documents_updated_at
AFTER UPDATE ON documents
FOR EACH ROW
BEGIN
    UPDATE documents SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_users_updated_at
AFTER UPDATE ON users
FOR EACH ROW
BEGIN
    UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_roles_updated_at
AFTER UPDATE ON roles
FOR EACH ROW
BEGIN
    UPDATE roles SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_groups_updated_at
AFTER UPDATE ON groups
FOR EACH ROW
BEGIN
    UPDATE groups SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_folder_acl_updated_at
AFTER UPDATE ON folder_acl
FOR EACH ROW
BEGIN
    UPDATE folder_acl SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_assessments_updated_at
AFTER UPDATE ON assessments
FOR EACH ROW
BEGIN
    UPDATE assessments SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_assessment_assignments_updated_at
AFTER UPDATE ON assessment_assignments
FOR EACH ROW
BEGIN
    UPDATE assessment_assignments SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_document_files_updated_at
AFTER UPDATE ON document_files
FOR EACH ROW
BEGIN
    UPDATE document_files SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_chat_sessions_updated_at
AFTER UPDATE ON chat_sessions
FOR EACH ROW
BEGIN
    UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_chat_messages_insert_stats
AFTER INSERT ON chat_messages
FOR EACH ROW
BEGIN
    UPDATE chat_sessions
    SET message_count = COALESCE(message_count, 0) + 1,
        last_activity = NEW.created_at,
        updated_at = NEW.created_at
    WHERE id = NEW.session_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_chat_messages_delete_stats
AFTER DELETE ON chat_messages
FOR EACH ROW
BEGIN
    UPDATE chat_sessions
    SET message_count = CASE WHEN message_count > 0 THEN message_count - 1 ELSE 0 END,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.session_id;
END;
"""


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Type {type(value).__name__} is not JSON serializable")


def _normalize_db_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, default=_json_default)
    if isinstance(value, bool):
        return int(value)
    return value


def _normalize_params(params):
    if params is None:
        return None
    if isinstance(params, dict):
        return {k: _normalize_db_value(v) for k, v in params.items()}
    return tuple(_normalize_db_value(v) for v in params)


def _translate_sql(sql: str) -> str:
    translated = sql.replace("%s", "?")
    translated = re.sub(r"\bNOW\(\)", "CURRENT_TIMESTAMP", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bILIKE\b", "LIKE", translated, flags=re.IGNORECASE)
    translated = re.sub(r"::[A-Za-z_][A-Za-z0-9_\[\]]*", "", translated)
    translated = translated.replace("TRUE", "1").replace("FALSE", "0")
    translated = translated.replace("gen_random_uuid()", "lower(hex(randomblob(16)))")
    return translated


def _decode_value(key, value):
    if value is None:
        return None
    if key in JSON_COLUMNS and isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
    if key in TIMESTAMP_COLUMNS and isinstance(value, str):
        try:
            return _convert_timestamp(value.encode("utf-8"))
        except Exception:
            return value
    return value


class ResultRow(dict):
    def __init__(self, data):
        super().__init__(data)
        self._ordered_keys = list(data.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            key = self._ordered_keys[key]
        return super().__getitem__(key)


class CompatCursor:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._cursor = conn.cursor()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def execute(self, sql, params=None):
        translated = _translate_sql(sql)
        normalized = _normalize_params(params)
        if normalized is None:
            self._cursor.execute(translated)
        else:
            self._cursor.execute(translated, normalized)
        return self

    def executescript(self, sql):
        self._cursor.executescript(sql)
        return self

    def _convert_row(self, row):
        if row is None:
            return None
        data = {key: _decode_value(key, row[key]) for key in row.keys()}
        return ResultRow(data)

    def fetchone(self):
        return self._convert_row(self._cursor.fetchone())

    def fetchall(self):
        return [self._convert_row(row) for row in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def close(self):
        self._cursor.close()


class CompatConnection:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.autocommit = False

    def cursor(self, cursor_factory=None):
        return CompatCursor(self._conn)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _bootstrap_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        """
        INSERT OR IGNORE INTO roles (id, name, description)
        VALUES
            ('role-admin', 'Admin', 'Full access'),
            ('role-user', 'User', 'Standard user'),
            ('role-guest', 'Guest', 'Guest user with limited access');
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO users (id, email, display_name)
        VALUES ('user-guest', 'guest@system.local', 'Guest User');
        """
    )
    conn.commit()


def ensure_database():
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(
            DB_PATH,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            _bootstrap_schema(conn)
        finally:
            conn.close()
        _initialized = True


def get_pool():
    ensure_database()
    return {"engine": "sqlite", "path": DB_PATH}


@contextmanager
def get_db_connection(autocommit=False):
    ensure_database()
    conn = sqlite3.connect(
        DB_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    wrapped = CompatConnection(conn)
    wrapped.autocommit = autocommit
    try:
        yield wrapped
        if autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_db_cursor(autocommit=False):
    with get_db_connection(autocommit=autocommit) as conn:
        cur = conn.cursor()
        try:
            yield cur
            if not autocommit:
                conn.commit()
        except Exception:
            if not autocommit:
                conn.rollback()
            raise
        finally:
            cur.close()


def run_query(query: str, params=None) -> list:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if cur.description:
                return cur.fetchall()
            conn.commit()
            return []


def execute_dml(sql: str, params=None) -> int:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rowcount = cur.rowcount
        conn.commit()
    return rowcount


def execute_sql(sql, params=None, commit=True):
    with get_db_connection(autocommit=not commit) as conn:
        cur = conn.cursor()
        try:
            if params is None:
                cur.executescript(_translate_sql(sql))
            else:
                cur.execute(sql, params)
            if commit and not conn.autocommit:
                conn.commit()
        finally:
            cur.close()


class MigrationError(Exception):
    pass


class MigrationManager:
    def __init__(self, migrations_dir=MIGRATIONS_DIR):
        self.migrations_dir = migrations_dir
        os.makedirs(self.migrations_dir, exist_ok=True)

    def ensure_migrations_table(self):
        ensure_database()

    def _migration_files(self):
        return sorted(glob.glob(os.path.join(self.migrations_dir, "*.sql")))

    def _file_checksum(self, path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _is_applied(self, filename, checksum):
        res = run_query(
            "SELECT 1 FROM schema_migrations WHERE filename = %s AND checksum = %s LIMIT 1;",
            (filename, checksum),
        )
        return len(res) > 0

    def applied_migrations(self):
        return run_query("SELECT filename, checksum, applied_at FROM schema_migrations ORDER BY applied_at;")

    def apply_migration_file(self, path, backup_before=False):
        filename = os.path.basename(path)
        checksum = self._file_checksum(path)

        if self._is_applied(filename, checksum):
            print(f"[skip] {filename} (already applied)")
            return

        if backup_before:
            self.backup_db(suffix=f"before_{filename}")

        sql = open(path, "r", encoding="utf-8").read()
        with get_db_connection() as conn:
            cur = conn.cursor()
            try:
                cur.executescript(_translate_sql(sql))
                cur.execute(
                    "INSERT INTO schema_migrations (filename, checksum, applied_at) VALUES (%s, %s, CURRENT_TIMESTAMP);",
                    (filename, checksum),
                )
                conn.commit()
                print(f"[ok] {filename}")
            except Exception as e:
                conn.rollback()
                raise MigrationError(f"Failed to apply {filename}: {e}") from e
            finally:
                cur.close()

    def apply_all(self, backup_before_each=False):
        self.ensure_migrations_table()
        bootstrap_checksum = hashlib.sha256(SCHEMA_SQL.encode("utf-8")).hexdigest()
        if not self._is_applied("sqlite_bootstrap", bootstrap_checksum):
            run_query(
                "INSERT INTO schema_migrations (filename, checksum, applied_at) VALUES (%s, %s, CURRENT_TIMESTAMP)",
                ("sqlite_bootstrap", bootstrap_checksum),
            )
        print("[ok] SQLite schema ensured at", DB_PATH)

    def create_migration_template(self, name):
        files = self._migration_files()
        last_num = 0
        if files:
            last = os.path.basename(files[-1])
            try:
                last_num = int(last.split("_", 1)[0])
            except Exception:
                last_num = 0
        new_num = last_num + 1
        filename = f"{new_num:04d}_{name}.sql"
        path = os.path.join(self.migrations_dir, filename)
        if os.path.exists(path):
            raise FileExistsError(path)
        template = f"""-- Migration: {filename}
-- Created: {datetime.now(timezone.utc).isoformat()}
BEGIN;

-- Write SQLite-compatible SQL here.

COMMIT;
"""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(template)
        print("Created:", path)
        return path

    def backup_db(self, suffix=None, out_dir="backups"):
        ensure_database()
        out_dir = os.path.abspath(out_dir)
        os.makedirs(out_dir, exist_ok=True)
        ts = suffix or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"sqlite_backup_{ts}.db"
        path = os.path.join(out_dir, filename)
        shutil.copy2(DB_PATH, path)
        print("[backup] copied DB to", path)
        return path


INITIAL_MIGRATION_SQL = SCHEMA_SQL


def ensure_initial_migration_file(migrations_dir=MIGRATIONS_DIR, content=INITIAL_MIGRATION_SQL):
    os.makedirs(migrations_dir, exist_ok=True)
    path = os.path.join(migrations_dir, "0001_initial.sql")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return path


def cli_apply_all(backup_before_each=False):
    mm = MigrationManager()
    ensure_initial_migration_file(mm.migrations_dir)
    mm.apply_all(backup_before_each)


def cli_create_template(name):
    mm = MigrationManager()
    mm.create_migration_template(name)


def cli_list_applied():
    mm = MigrationManager()
    mm.ensure_migrations_table()
    applied = mm.applied_migrations()
    print("Applied migrations:")
    for row in applied:
        print(row["filename"], row["checksum"], row["applied_at"])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd in ("migrate", "apply"):
        backup = "--backup" in sys.argv
        cli_apply_all(backup_before_each=backup)
    elif cmd == "create":
        if len(sys.argv) < 3:
            print("Usage: python db.py create <name_for_migration>")
            sys.exit(1)
        cli_create_template(sys.argv[2])
    elif cmd == "list":
        cli_list_applied()
    else:
        print("Usage: python db.py [migrate|create|list]")
