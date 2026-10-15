"""Database schema initialization for SmartSort."""

import sqlite3
from pathlib import Path
from typing import Optional

from app.database.connection import get_connection, _init_lock


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize the database schema idempotently.
    
    This function creates the required tables if they do not already exist.
    It is safe to call multiple times.
    """
    with _init_lock:
        with get_connection(db_path) as conn:
            # Table: scans
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scans (
                    id TEXT PRIMARY KEY,
                    root_path TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    file_count INTEGER NOT NULL DEFAULT 0,
                    folder_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL
                )
                """
            )

            # Table: files
            # Note: hash is nullable and will be populated in Phase 4.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    modified_time TEXT NOT NULL,
                    hash TEXT,
                    mime_type TEXT,
                    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
                )
                """
            )

            # Index for fast lookups by scan_id
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_scan_id ON files(scan_id)")
            
            # Index for fast lookups by extension (useful for stats/filtering)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension)")

            # Table: protected_paths
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS protected_paths (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    protection_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

            # Table: operations (Foundation for future phases)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id TEXT,
                    task_id TEXT,
                    operation_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    destination TEXT,
                    status TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    verified_at TEXT,
                    error_details TEXT,
                    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE SET NULL
                )
                """
            )

            # Table: preferences
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            # Table: document_content (Phase 5)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_content (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    text_content TEXT,
                    character_count INTEGER,
                    page_count INTEGER,
                    extracted_at TEXT NOT NULL,
                    error_info TEXT,
                    source_hash TEXT,
                    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
                )
                """
            )

            # Migration: Add source_hash column if it doesn't exist
            try:
                conn.execute("ALTER TABLE document_content ADD COLUMN source_hash TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Table: file_analysis (Phase 6)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL UNIQUE,
                    version INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    size_bucket TEXT NOT NULL,
                    age_days INTEGER NOT NULL,
                    age_bucket TEXT NOT NULL,
                    name_tokens TEXT,
                    is_copy BOOLEAN NOT NULL DEFAULT 0,
                    is_temp BOOLEAN NOT NULL DEFAULT 0,
                    numeric_suffix INTEGER,
                    depth INTEGER NOT NULL,
                    is_root_level BOOLEAN NOT NULL DEFAULT 0,
                    parent_dir TEXT,
                    path_tokens TEXT,
                    has_duplicates BOOLEAN NOT NULL DEFAULT 0,
                    char_count INTEGER,
                    word_count INTEGER,
                    line_count INTEGER,
                    reading_time_mins INTEGER,
                    analyzed_at TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    source_mtime TEXT NOT NULL,
                    source_hash TEXT,
                    source_content_hash TEXT,
                    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
                )
                """
            )

            # Table: organization_plans (Phase 8)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS organization_plans (
                    id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL,
                    objective TEXT,
                    created_at TEXT NOT NULL,
                    warnings TEXT NOT NULL,
                    protected_conflicts TEXT NOT NULL,
                    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
                )
                """
            )

            # Table: organization_operations (Phase 8)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS organization_operations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    reason TEXT,
                    confidence TEXT NOT NULL,
                    FOREIGN KEY (plan_id) REFERENCES organization_plans(id) ON DELETE CASCADE
                )
                """
            )

            # Migrations for Phase 8 organization_operations (content-aware fields)
            for _col, _default in [
                ("explanation", "NULL"),
                ("topic", "NULL"),
                ("folder_exists", "NULL"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE organization_operations ADD COLUMN {_col} TEXT DEFAULT {_default}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists

            # Human-rules migrations: new columns on organization_operations
            for _col, _default in [
                ("rule", "NULL"),
                ("confidence_score", "NULL"),
                ("flags", "NULL"),
                ("date_source", "NULL"),
                ("original_name", "NULL"),
                ("duplicate_of", "NULL"),
                ("likely_newer", "NULL"),
                ("needs_review", "NULL"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE organization_operations ADD COLUMN {_col} TEXT DEFAULT {_default}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists

            # Migrations for Phase 8 organization_plans (summary fields)
            for _col, _default in [
                ("detected_topics", "'{}'"),
                ("new_folders_proposed", "0"),
                ("existing_folders_reused", "0"),
                ("unclassified_files", "0"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE organization_plans ADD COLUMN {_col} TEXT DEFAULT {_default}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists

            # Human-rules migrations: new columns on organization_plans
            for _col, _default in [
                ("structure", "NULL"),
                ("rename", "0"),
                ("archive_days", "NULL"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE organization_plans ADD COLUMN {_col} TEXT DEFAULT {_default}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists

            # Indexes for Phase 8
            conn.execute("CREATE INDEX IF NOT EXISTS idx_org_operations_plan_id ON organization_operations(plan_id)")

            # Table: agent_runs (Phase 9)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    scan_id TEXT,
                    objective TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_step TEXT,
                    progress REAL NOT NULL DEFAULT 0.0,
                    plan_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT,
                    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE SET NULL,
                    FOREIGN KEY (plan_id) REFERENCES organization_plans(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_state ON agent_runs(state)")

            # Table: operation_approvals (Phase 11)
            # One row per organization_operation; stores the CURRENT decision.
            # History of changes is in operation_approval_history.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id INTEGER NOT NULL UNIQUE,
                    plan_id TEXT NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'PENDING',
                    comment TEXT,
                    decided_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (operation_id) REFERENCES organization_operations(id) ON DELETE CASCADE,
                    FOREIGN KEY (plan_id) REFERENCES organization_plans(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_approvals_plan_id ON operation_approvals(plan_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_approvals_operation_id ON operation_approvals(operation_id)"
            )

            # Table: operation_approval_history (Phase 11)
            # Append-only immutable audit log; never updated or deleted.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_approval_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    approval_id INTEGER NOT NULL,
                    previous_decision TEXT NOT NULL,
                    new_decision TEXT NOT NULL,
                    comment TEXT,
                    changed_at TEXT NOT NULL,
                    FOREIGN KEY (approval_id) REFERENCES operation_approvals(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_history_approval_id "
                "ON operation_approval_history(approval_id)"
            )

            # Table: operation_executions (Phase 12)
            # Tracks execution lifecycle (PENDING -> IN_PROGRESS -> COMPLETED/FAILED/SKIPPED)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id INTEGER NOT NULL UNIQUE,
                    plan_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    error_code TEXT,
                    error_message TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (operation_id) REFERENCES organization_operations(id) ON DELETE CASCADE,
                    FOREIGN KEY (plan_id) REFERENCES organization_plans(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_executions_plan_id ON operation_executions(plan_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_executions_operation_id ON operation_executions(operation_id)")

            # Table: operation_verifications (Phase 13)
            # Tracks verification outcomes (VERIFIED, FAILED, INCONCLUSIVE)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_verifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id INTEGER NOT NULL UNIQUE,
                    plan_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT,
                    verified_at TEXT NOT NULL,
                    FOREIGN KEY (operation_id) REFERENCES organization_operations(id) ON DELETE CASCADE,
                    FOREIGN KEY (plan_id) REFERENCES organization_plans(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_verifications_plan_id ON operation_verifications(plan_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_verifications_operation_id ON operation_verifications(operation_id)")

            # Table: operation_verification_history (Phase 13)
            # Append-only immutable audit log; never updated or deleted.
            # Mirrors the Phase 11 operation_approval_history convention.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_verification_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id INTEGER NOT NULL,
                    plan_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT,
                    verified_at TEXT NOT NULL,
                    FOREIGN KEY (operation_id) REFERENCES organization_operations(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_verification_history_operation_id "
                "ON operation_verification_history(operation_id)"
            )
