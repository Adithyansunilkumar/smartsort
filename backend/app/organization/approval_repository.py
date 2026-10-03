"""Phase 11 — Approval Repository.

Encapsulates all SQL for persisting and querying operation approvals
and the immutable approval history log.

Design decisions
----------------
* ``operation_approvals`` has at most ONE row per operation (UNIQUE constraint
  on operation_id).  It stores the *current* decision.
* ``operation_approval_history`` is append-only; its rows are never updated or
  deleted.  Every decision change (including the initial creation) writes a
  history entry so the full audit trail is preserved.
* Idempotency: submitting the same decision twice for the same operation is
  silently accepted and returns the existing record unchanged (no history entry
  added for a no-op).
* Decision change (APPROVED → REJECTED or vice versa): the current row is
  updated in-place and a new history entry is appended.
* PENDING → APPROVED/REJECTED: the current row is updated and a history entry
  is appended.
* Re-submitting PENDING (i.e. deleting a prior decision) is NOT supported via
  this API; PENDING is only the default initial state.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.database.connection import get_connection


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ApprovalRepository:
    """Repository for Phase 11 operation approvals."""

    # ------------------------------------------------------------------
    # Creation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def ensure_approval_record(operation_id: int, plan_id: str) -> Dict[str, Any]:
        """Ensure an approval row exists for *operation_id*.

        If no row exists, one is created with decision=PENDING.
        If a row already exists, it is returned unchanged.
        Returns the approval row as a dict.
        """
        now = _utcnow()
        with get_connection() as conn:
            # Try insert; ignore if already present (UNIQUE on operation_id)
            conn.execute(
                """
                INSERT OR IGNORE INTO operation_approvals
                    (operation_id, plan_id, decision, comment, decided_at, created_at)
                VALUES (?, ?, 'PENDING', NULL, ?, ?)
                """,
                (operation_id, plan_id, now, now),
            )
            cursor = conn.execute(
                "SELECT * FROM operation_approvals WHERE operation_id = ?",
                (operation_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(
                    f"Failed to create or retrieve approval record for operation {operation_id}"
                )
            return dict(row)

    @staticmethod
    def seed_pending_for_plan(plan_id: str, operation_ids: List[int]) -> None:
        """Bulk-seed PENDING approval records for all operations in a plan.

        Skips any operation that already has an approval row.
        Called right after a plan is persisted so that all operations
        start with a visible PENDING state.
        """
        if not operation_ids:
            return
        now = _utcnow()
        rows = [(op_id, plan_id, "PENDING", None, now, now) for op_id in operation_ids]
        with get_connection() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO operation_approvals
                    (operation_id, plan_id, decision, comment, decided_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    # ------------------------------------------------------------------
    # Decision recording
    # ------------------------------------------------------------------

    @staticmethod
    def record_decision(
        operation_id: int,
        plan_id: str,
        new_decision: str,
        comment: Optional[str],
    ) -> Dict[str, Any]:
        """Record a new APPROVED or REJECTED decision for *operation_id*.

        Decision semantics
        ------------------
        * If no approval row exists, one is created (PENDING) and then
          immediately updated with *new_decision*.
        * If the current decision equals *new_decision*, this is a no-op:
          the existing row is returned without writing a history entry.
        * If the decision is different, the current row is updated and a
          history entry is appended atomically.

        Returns the updated approval row as a dict.
        Raises ValueError if *new_decision* is "PENDING" (clients may not
        explicitly set PENDING; it is only the initial default).
        """
        if new_decision == "PENDING":
            raise ValueError(
                "PENDING is not a valid client decision. "
                "Submit APPROVED or REJECTED."
            )

        now = _utcnow()

        with get_connection() as conn:
            # Ensure the row exists (idempotent insert)
            conn.execute(
                """
                INSERT OR IGNORE INTO operation_approvals
                    (operation_id, plan_id, decision, comment, decided_at, created_at)
                VALUES (?, ?, 'PENDING', NULL, ?, ?)
                """,
                (operation_id, plan_id, now, now),
            )

            # Fetch current state
            cursor = conn.execute(
                "SELECT * FROM operation_approvals WHERE operation_id = ?",
                (operation_id,),
            )
            current_row = cursor.fetchone()
            if current_row is None:
                raise RuntimeError(
                    f"Approval row missing for operation {operation_id} after upsert"
                )
            current = dict(current_row)
            current_decision = current["decision"]
            approval_id = current["id"]

            # Idempotent: same decision → return unchanged, no history entry
            if current_decision == new_decision:
                return current

            # Decision changed: update current row + append history
            conn.execute(
                """
                UPDATE operation_approvals
                SET decision = ?, comment = ?, decided_at = ?
                WHERE id = ?
                """,
                (new_decision, comment, now, approval_id),
            )
            conn.execute(
                """
                INSERT INTO operation_approval_history
                    (approval_id, previous_decision, new_decision, comment, changed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (approval_id, current_decision, new_decision, comment, now),
            )

            # Fetch and return the updated row
            cursor2 = conn.execute(
                "SELECT * FROM operation_approvals WHERE id = ?",
                (approval_id,),
            )
            updated = cursor2.fetchone()
            assert updated is not None
            return dict(updated)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @staticmethod
    def get_approval_for_operation(operation_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve the current approval record for a single operation."""
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM operation_approvals WHERE operation_id = ?",
                (operation_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_approvals_for_plan(plan_id: str) -> List[Dict[str, Any]]:
        """Retrieve all current approval records for the given plan.

        Joins with organization_operations to return both operation
        metadata and the current approval state in a single pass.
        """
        with get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    oo.id        AS operation_id,
                    oo.plan_id,
                    oo.type,
                    oo.source,
                    oo.destination,
                    oo.reason,
                    oo.confidence,
                    COALESCE(oa.decision, 'PENDING') AS approval_decision,
                    oa.comment,
                    oa.decided_at,
                    oa.id        AS approval_id,
                    oa.created_at AS approval_created_at
                FROM organization_operations oo
                LEFT JOIN operation_approvals oa ON oo.id = oa.operation_id
                WHERE oo.plan_id = ?
                ORDER BY oo.id ASC
                """,
                (plan_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_approval_history(operation_id: int) -> List[Dict[str, Any]]:
        """Retrieve the full immutable history for a single operation's approval."""
        with get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT h.*
                FROM operation_approval_history h
                JOIN operation_approvals a ON h.approval_id = a.id
                WHERE a.operation_id = ?
                ORDER BY h.id ASC
                """,
                (operation_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
