"""Repository layer for database operations.

This module encapsulates all raw SQL queries and parameter bindings.
It abstracts database interactions for the rest of the application.
"""

from typing import Any, Dict, List, Optional
from app.filesystem.scanner import ScanResult
from app.database.connection import get_connection
import json


class ScanRepository:
    """Repository for persisting and retrieving filesystem scans."""

    @staticmethod
    def save_scan(scan_result: ScanResult) -> None:
        """Persist a complete ScanResult and its associated files.
        
        The entire operation runs within a single transaction.
        """
        with get_connection() as conn:
            # Insert the scan record
            conn.execute(
                """
                INSERT INTO scans (
                    id, root_path, started_at, completed_at,
                    file_count, folder_count, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_result.scan_id,
                    scan_result.root_display,
                    scan_result.scanned_at,
                    scan_result.scanned_at,  # Synchronous, so started=completed for now
                    scan_result.file_count,
                    scan_result.directory_count,
                    scan_result.status.value,
                ),
            )

            # Insert all files in bulk
            if scan_result.files:
                file_records = [
                    (
                        scan_result.scan_id,
                        f.relative_path,
                        f.name,
                        f.extension,
                        f.size_bytes,
                        f.modified_at,
                        None,  # hash is computed in later phases
                        f.mime_type,
                    )
                    for f in scan_result.files
                ]
                
                conn.executemany(
                    """
                    INSERT INTO files (
                        scan_id, path, name, extension, size, modified_time, hash, mime_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    file_records,
                )

    @staticmethod
    def get_files_by_scan_id(scan_id: str) -> List[Dict[str, Any]]:
        """Retrieve all file records for a given scan."""
        with get_connection() as conn:
            cursor = conn.execute("SELECT * FROM files WHERE scan_id = ?", (scan_id,))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def update_file_hashes(hash_updates: List[tuple[str, int]]) -> None:
        """Bulk update hashes for specific files.

        Args:
            hash_updates: A list of tuples containing (hash_digest, file_id).
        """
        if not hash_updates:
            return

        with get_connection() as conn:
            conn.executemany(
                "UPDATE files SET hash = ? WHERE id = ?",
                hash_updates,
            )


class PreferenceRepository:
    """Repository for key-value preferences."""

    @staticmethod
    def set_preference(key: str, value: Any) -> None:
        """Store or update a preference. The value is JSON-encoded."""
        encoded_value = json.dumps(value)
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO preferences (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, encoded_value),
            )

    @staticmethod
    def get_preference(key: str, default: Any = None) -> Any:
        """Retrieve a preference, returning the default if not found."""
        with get_connection() as conn:
            cursor = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return json.loads(row["value"])
            return default


class ProtectedPathRepository:
    """Repository for managing protected paths."""
    
    @staticmethod
    def add_protected_path(path: str, protection_type: str, created_at: str) -> None:
        """Add a protected path.
        
        Important: The caller MUST validate the path via FolderAccessService 
        before calling this method to ensure it's within the authorized root.
        """
        with get_connection() as conn:
            # Ignore if it already exists
            conn.execute(
                """
                INSERT OR IGNORE INTO protected_paths (path, protection_type, created_at)
                VALUES (?, ?, ?)
                """,
                (path, protection_type, created_at),
            )

    @staticmethod
    def get_all_protected_paths() -> List[Dict[str, Any]]:
        """Retrieve all protected paths."""
        with get_connection() as conn:
            cursor = conn.execute("SELECT * FROM protected_paths ORDER BY path ASC")
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def remove_protected_path(path: str) -> bool:
        """Remove a protected path by its stored path value.

        Returns True if a row was deleted, False if no matching row was found.
        """
        with get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM protected_paths WHERE path = ?",
                (path,),
            )
            return cursor.rowcount > 0


class OperationRepository:
    """Repository for operation history (Foundation for future phases)."""

    @staticmethod
    def add_operation(
        operation_type: str,
        source: str,
        status: str,
        created_at: str,
        scan_id: Optional[str] = None,
        task_id: Optional[str] = None,
        destination: Optional[str] = None,
        reason: Optional[str] = None,
        error_details: Optional[str] = None,
    ) -> None:
        """Record an operation in the history log."""
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO operations (
                    scan_id, task_id, operation_type, source, destination,
                    status, reason, created_at, error_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    task_id,
                    operation_type,
                    source,
                    destination,
                    status,
                    reason,
                    created_at,
                    error_details,
                ),
            )

class ContentRepository:
    """Repository for managing extracted document content."""

    @staticmethod
    def upsert_extraction_result(
        file_id: int,
        status: str,
        text_content: Optional[str],
        character_count: Optional[int],
        page_count: Optional[int],
        extracted_at: str,
        error_info: Optional[str],
        source_hash: Optional[str] = None
    ) -> None:
        """Insert or replace an extraction result for a specific file."""
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO document_content (
                    file_id, status, text_content, character_count, 
                    page_count, extracted_at, error_info, source_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    status=excluded.status,
                    text_content=excluded.text_content,
                    character_count=excluded.character_count,
                    page_count=excluded.page_count,
                    extracted_at=excluded.extracted_at,
                    error_info=excluded.error_info,
                    source_hash=excluded.source_hash
                """,
                (
                    file_id, status, text_content, character_count,
                    page_count, extracted_at, error_info, source_hash
                )
            )

    @staticmethod
    def get_extraction_results_for_scan(scan_id: str) -> List[Dict[str, Any]]:
        """Fetch all extraction results joined with file metadata for a scan."""
        with get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT f.id as file_id, f.path, c.status, c.character_count, 
                       c.page_count, c.error_info, c.extracted_at, c.source_hash
                FROM files f
                LEFT JOIN document_content c ON f.id = c.file_id
                WHERE f.scan_id = ?
                """,
                (scan_id,)
            )
            return [dict(row) for row in cursor.fetchall()]


class AnalysisRepository:
    """Repository for managing file analysis results (Phase 6)."""

    @staticmethod
    def upsert_file_analysis(analysis_data: Dict[str, Any]) -> None:
        """Insert or replace a file analysis record."""
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO file_analysis (
                    file_id, version, category, size_bytes, size_bucket,
                    age_days, age_bucket, name_tokens, is_copy, is_temp,
                    numeric_suffix, depth, is_root_level, parent_dir, path_tokens,
                    has_duplicates, char_count, word_count, line_count,
                    reading_time_mins, analyzed_at, source_size, source_mtime,
                    source_hash, source_content_hash
                ) VALUES (
                    :file_id, :version, :category, :size_bytes, :size_bucket,
                    :age_days, :age_bucket, :name_tokens, :is_copy, :is_temp,
                    :numeric_suffix, :depth, :is_root_level, :parent_dir, :path_tokens,
                    :has_duplicates, :char_count, :word_count, :line_count,
                    :reading_time_mins, :analyzed_at, :source_size, :source_mtime,
                    :source_hash, :source_content_hash
                )
                ON CONFLICT(file_id) DO UPDATE SET
                    version=excluded.version,
                    category=excluded.category,
                    size_bytes=excluded.size_bytes,
                    size_bucket=excluded.size_bucket,
                    age_days=excluded.age_days,
                    age_bucket=excluded.age_bucket,
                    name_tokens=excluded.name_tokens,
                    is_copy=excluded.is_copy,
                    is_temp=excluded.is_temp,
                    numeric_suffix=excluded.numeric_suffix,
                    depth=excluded.depth,
                    is_root_level=excluded.is_root_level,
                    parent_dir=excluded.parent_dir,
                    path_tokens=excluded.path_tokens,
                    has_duplicates=excluded.has_duplicates,
                    char_count=excluded.char_count,
                    word_count=excluded.word_count,
                    line_count=excluded.line_count,
                    reading_time_mins=excluded.reading_time_mins,
                    analyzed_at=excluded.analyzed_at,
                    source_size=excluded.source_size,
                    source_mtime=excluded.source_mtime,
                    source_hash=excluded.source_hash,
                    source_content_hash=excluded.source_content_hash
                """,
                analysis_data
            )

    @staticmethod
    def get_analysis_results_for_scan(scan_id: str) -> List[Dict[str, Any]]:
        """Fetch all analysis results for a given scan."""
        with get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT a.*, f.path as relative_path, f.name 
                FROM files f
                LEFT JOIN file_analysis a ON f.id = a.file_id
                WHERE f.scan_id = ?
                """,
                (scan_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

class OrganizationRepository:
    """Repository for persisting Phase 8 Organization Plans."""

    @staticmethod
    def save_plan(
        plan_id: str,
        scan_id: str,
        objective: Optional[str],
        created_at: str,
        warnings: List[str],
        protected_conflicts: List[str],
        operations: List[Dict[str, Any]],
        detected_topics: Optional[Dict[str, int]] = None,
        new_folders_proposed: int = 0,
        existing_folders_reused: int = 0,
        unclassified_files: int = 0,
        **kwargs,
    ) -> None:
        """Persist a complete organization plan and its operations in one transaction.

        Extra keyword args forwarded from the plan model:
            structure, rename, archive_days
        """
        import json
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO organization_plans (
                    id, scan_id, objective, created_at, warnings, protected_conflicts,
                    detected_topics, new_folders_proposed, existing_folders_reused,
                    unclassified_files, structure, rename, archive_days
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    scan_id,
                    objective,
                    created_at,
                    json.dumps(warnings),
                    json.dumps(protected_conflicts),
                    json.dumps(detected_topics or {}),
                    new_folders_proposed,
                    existing_folders_reused,
                    unclassified_files,
                    kwargs.get("structure"),
                    1 if kwargs.get("rename") else 0,
                    kwargs.get("archive_days"),
                )
            )

            if operations:
                op_records = [
                    (
                        plan_id,
                        op["type"],
                        op["source"],
                        op["destination"],
                        op.get("reason"),
                        op["confidence"],
                        op.get("explanation"),
                        op.get("topic"),
                        op.get("folder_exists"),
                        op.get("rule"),
                        op.get("confidence_score"),
                        json.dumps(op.get("flags") or []) if op.get("flags") is not None else None,
                        op.get("date_source"),
                        op.get("original_name"),
                        op.get("duplicate_of"),
                        1 if op.get("likely_newer") else None,
                        1 if op.get("needs_review") else None,
                    )
                    for op in operations
                ]
                conn.executemany(
                    """
                    INSERT INTO organization_operations (
                        plan_id, type, source, destination, reason, confidence,
                        explanation, topic, folder_exists,
                        rule, confidence_score, flags, date_source,
                        original_name, duplicate_of, likely_newer, needs_review
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    op_records
                )

                # Phase 11: seed PENDING approval records for each new operation
                # so every operation has a visible PENDING state from creation.
                cursor = conn.execute(
                    "SELECT id FROM organization_operations WHERE plan_id = ?",
                    (plan_id,)
                )
                op_ids = [row[0] for row in cursor.fetchall()]
                import datetime as _dt
                now_iso = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()
                approval_rows = [
                    (op_id, plan_id, "PENDING", None, now_iso, now_iso)
                    for op_id in op_ids
                ]
                if approval_rows:
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO operation_approvals
                            (operation_id, plan_id, decision, comment, decided_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        approval_rows
                    )

                # Phase 12: seed PENDING execution records
                execution_rows = [
                    (op_id, plan_id, "PENDING")
                    for op_id in op_ids
                ]
                if execution_rows:
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO operation_executions
                            (operation_id, plan_id, status)
                        VALUES (?, ?, ?)
                        """,
                        execution_rows
                    )

    @staticmethod
    def get_plan(plan_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a complete plan by ID."""
        import json
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM organization_plans WHERE id = ?", (plan_id,)
            )
            plan_row = cursor.fetchone()
            if not plan_row:
                return None

            plan_dict = dict(plan_row)
            plan_dict["plan_id"] = plan_dict.pop("id")
            plan_dict["warnings"] = json.loads(plan_dict["warnings"])
            plan_dict["protected_conflicts"] = json.loads(plan_dict["protected_conflicts"])
            # New summary fields (may be absent in older DB rows)
            raw_topics = plan_dict.get("detected_topics") or "{}"
            plan_dict["detected_topics"] = json.loads(raw_topics) if isinstance(raw_topics, str) else raw_topics
            plan_dict["new_folders_proposed"] = plan_dict.get("new_folders_proposed") or 0
            plan_dict["existing_folders_reused"] = plan_dict.get("existing_folders_reused") or 0
            plan_dict["unclassified_files"] = plan_dict.get("unclassified_files") or 0
            plan_dict["structure"] = plan_dict.get("structure")
            plan_dict["rename"] = bool(plan_dict.get("rename"))
            plan_dict["archive_days"] = plan_dict.get("archive_days")

            op_cursor = conn.execute(
                "SELECT * FROM organization_operations WHERE plan_id = ?", (plan_id,)
            )
            ops = []
            for row in op_cursor.fetchall():
                op = dict(row)
                # Deserialize flags (stored as JSON list or comma-string)
                raw_flags = op.get("flags")
                if raw_flags:
                    try:
                        parsed = json.loads(raw_flags)
                        if isinstance(parsed, list):
                            op["flags"] = ",".join(parsed) if parsed else None
                        else:
                            op["flags"] = raw_flags
                    except Exception:
                        op["flags"] = raw_flags  # keep as-is (already comma-string)
                else:
                    op["flags"] = None
                # Coerce numeric fields
                if op.get("confidence_score") is not None:
                    try:
                        op["confidence_score"] = float(op["confidence_score"])
                    except (TypeError, ValueError):
                        op["confidence_score"] = None
                op["likely_newer"] = bool(op.get("likely_newer")) if op.get("likely_newer") else None
                op["needs_review"] = bool(op.get("needs_review")) if op.get("needs_review") else None
                ops.append(op)
            plan_dict["operations"] = ops

            return plan_dict


# Re-export AgentRepository from app.agent.repository
from app.agent.repository import AgentRepository  # noqa: E402

