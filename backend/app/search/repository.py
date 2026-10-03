"""Search repository — retrieves persisted data for Phase 7.

All SQL lives here, never inside the search algorithms.
"""

from typing import Any, Dict, List, Optional
from app.database.connection import get_connection


class SearchRepository:
    """Retrieves scan / file / content / analysis data for search.

    Uses a single batch join per scan to avoid N+1 queries.
    """

    @staticmethod
    def scan_exists(scan_id: str) -> bool:
        """Return True if the scan ID is present in the database."""
        with get_connection() as conn:
            cur = conn.execute("SELECT 1 FROM scans WHERE id = ?", (scan_id,))
            return cur.fetchone() is not None

    @staticmethod
    def get_searchable_records(scan_id: str) -> List[Dict[str, Any]]:
        """Fetch all data needed to build the search index for a scan.

        Returns one row per file with:
        - file metadata (id, name, path, extension, size, modified_time, hash)
        - extracted text content (text_content) — NULL if not available
        - analysis category — NULL if not analyzed
        - duplicate flag

        Uses LEFT JOINs to include all files even without content/analysis.
        The duplicate_hash_group subquery marks files whose hash appears > 1 time.
        """
        with get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    f.id                 AS file_id,
                    f.scan_id            AS scan_id,
                    f.name               AS name,
                    f.path               AS relative_path,
                    f.extension          AS extension,
                    f.size               AS size_bytes,
                    f.modified_time      AS modified_at,
                    f.hash               AS file_hash,
                    dc.text_content      AS text_content,
                    dc.status            AS content_status,
                    fa.category          AS category,
                    CASE
                        WHEN dup.hash IS NOT NULL THEN 1
                        ELSE 0
                    END                  AS has_duplicates
                FROM files f
                LEFT JOIN document_content dc ON f.id = dc.file_id
                    AND dc.status = 'SUCCESS'
                LEFT JOIN file_analysis fa ON f.id = fa.file_id
                LEFT JOIN (
                    SELECT hash
                    FROM files
                    WHERE scan_id = ? AND hash IS NOT NULL
                    GROUP BY hash
                    HAVING COUNT(*) > 1
                ) dup ON f.hash = dup.hash
                WHERE f.scan_id = ?
                ORDER BY f.id
                """,
                (scan_id, scan_id),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_file_by_id(scan_id: str, file_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a single file record (with content + analysis) verified
        to belong to the given scan."""
        with get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    f.id                 AS file_id,
                    f.scan_id            AS scan_id,
                    f.name               AS name,
                    f.path               AS relative_path,
                    f.extension          AS extension,
                    f.size               AS size_bytes,
                    f.modified_time      AS modified_at,
                    f.hash               AS file_hash,
                    dc.text_content      AS text_content,
                    dc.status            AS content_status,
                    fa.category          AS category,
                    CASE
                        WHEN dup.hash IS NOT NULL THEN 1
                        ELSE 0
                    END                  AS has_duplicates
                FROM files f
                LEFT JOIN document_content dc ON f.id = dc.file_id
                    AND dc.status = 'SUCCESS'
                LEFT JOIN file_analysis fa ON f.id = fa.file_id
                LEFT JOIN (
                    SELECT hash
                    FROM files
                    WHERE scan_id = ? AND hash IS NOT NULL
                    GROUP BY hash
                    HAVING COUNT(*) > 1
                ) dup ON f.hash = dup.hash
                WHERE f.scan_id = ? AND f.id = ?
                """,
                (scan_id, scan_id, file_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
